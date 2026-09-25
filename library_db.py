"""
ULTIMATE TRANSLATOR - Library storage
SQLite storage for the personal book library: private libraries, nested
folders, books, tags, trash and translation jobs.

Every function opens its own connection, so the module is safe to use from
request threads, background threads and several gunicorn workers at once
(WAL journal + busy timeout, writes serialised with BEGIN IMMEDIATE).
"""

import os
import re
import json
import time
import uuid
import zlib
import sqlite3
import secrets
import logging
import unicodedata
from collections import defaultdict
from contextlib import contextmanager

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SCHEMA_VERSION = 1

COLOR_KEYS = (
    'red', 'orange', 'yellow', 'green', 'teal', 'blue',
    'indigo', 'purple', 'pink', 'brown', 'gray',
)
# Colors picked automatically for new tags (vivid ones only)
TAG_AUTO_COLORS = ('purple', 'blue', 'teal', 'green', 'orange', 'pink', 'indigo', 'yellow', 'red')

MAX_FOLDER_NAME = 120
MAX_TITLE = 300
MAX_AUTHOR = 200
MAX_TAG_NAME = 40
MAX_FOLDER_DEPTH = 24
TRASH_RETENTION_SECONDS = 30 * 24 * 3600
STALE_TRANSLATION_SECONDS = 30 * 60
LOG_TAIL = 200

# Crockford base32: no I, L, O, U -> easy to read and type
CODE_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
CODE_LENGTH = 20

_state = {'data_dir': None, 'db_path': None}
_UNSET = object()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS libraries (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES folders(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT,
    position REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL,
    trash_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(library_id, parent_id);

CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    author TEXT,
    original_filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    file_path TEXT NOT NULL,
    sha256 TEXT,
    color TEXT,
    favorite INTEGER NOT NULL DEFAULT 0,
    position REAL NOT NULL DEFAULT 0,
    language TEXT,
    summary TEXT,
    num_pages INTEGER,
    num_chapters INTEGER,
    total_words INTEGER,
    total_chars INTEGER,
    estimated_tokens INTEGER,
    has_cover INTEGER NOT NULL DEFAULT 0,
    tag_status TEXT NOT NULL DEFAULT 'pending',
    tag_method TEXT,
    tag_error TEXT,
    tag_updated_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL,
    trash_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_books_folder ON books(library_id, folder_id);
CREATE INDEX IF NOT EXISTS idx_books_sha ON books(library_id, sha256);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    color TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (library_id, name_key)
);

CREATE TABLE IF NOT EXISTS book_tags (
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at REAL NOT NULL,
    PRIMARY KEY (book_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_book_tags_tag ON book_tags(tag_id);

CREATE TABLE IF NOT EXISTS translations (
    id TEXT PRIMARY KEY,
    library_id TEXT REFERENCES libraries(id) ON DELETE CASCADE,
    book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
    source_lang TEXT,
    target_lang TEXT,
    provider TEXT,
    model TEXT,
    file_type TEXT,
    input_path TEXT,
    output_path TEXT,
    output_filename TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0,
    status_text TEXT,
    error TEXT,
    logs TEXT NOT NULL DEFAULT '[]',
    logs_total INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_translations_book ON translations(book_id);
CREATE INDEX IF NOT EXISTS idx_translations_library ON translations(library_id, status);
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LibraryError(Exception):
    """A user-facing error with an HTTP status and an optional machine code."""

    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class NotFound(LibraryError):
    def __init__(self, message='Elemento non trovato'):
        super().__init__(message, 404, 'not_found')


# ---------------------------------------------------------------------------
# Setup & connections
# ---------------------------------------------------------------------------

def init(data_dir=None):
    """Create the data directories and the database schema (idempotent)."""
    data_dir = os.path.abspath(
        data_dir or os.getenv('LIBRARY_DATA_DIR') or os.path.join(BASE_DIR, 'data')
    )
    for sub in ('books', 'covers', 'translations', 'tmp'):
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)

    _state['data_dir'] = data_dir
    _state['db_path'] = os.path.join(data_dir, 'library.db')

    conn = _connect()
    try:
        try:
            conn.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError as e:
            logger.warning(f"Could not enable WAL journal: {e}")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    finally:
        conn.close()
    logger.info(f"Library storage ready in {data_dir}")


def _connect():
    if not _state['db_path']:
        raise RuntimeError('library_db.init() has not been called')
    conn = sqlite3.connect(_state['db_path'], timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


@contextmanager
def _read():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _write():
    conn = _connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.execute('COMMIT')
    except BaseException:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _ph(items):
    return ','.join('?' * len(items))


def _chunked(items, size=400):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------------------
# Paths & files
# ---------------------------------------------------------------------------

def data_dir():
    return _state['data_dir']


def abs_path(path):
    """Resolve a stored path (relative to the data dir, or absolute)."""
    if not path:
        return None
    if os.path.isabs(path):
        return path
    return os.path.join(_state['data_dir'], *path.split('/'))


def stored_path(path):
    """Paths inside the data dir are stored relative, so the dir can be moved."""
    if not path:
        return path
    root = os.path.realpath(_state['data_dir'])
    real = os.path.realpath(path)
    if real.startswith(root + os.sep):
        return os.path.relpath(real, root).replace(os.sep, '/')
    return path


def book_file_rel(library_id, book_id, ext):
    return f"books/{library_id}/{book_id}.{ext}"


def cover_rel(library_id, book_id):
    return f"covers/{library_id}/{book_id}.jpg"


def translation_rel(library_id, translation_id, ext):
    return f"translations/{library_id}/{translation_id}.{ext}"


def remove_files(paths):
    """Delete files that live inside the data dir; ignore missing ones."""
    root = os.path.realpath(_state['data_dir'])
    for path in paths:
        full = abs_path(path)
        if not full:
            continue
        real = os.path.realpath(full)
        if not real.startswith(root + os.sep):
            continue
        try:
            os.remove(real)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Could not delete {real}: {e}")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _strip_control(value):
    return ''.join(c for c in value if unicodedata.category(c)[0] != 'C')


def clean_name(value, max_len, label='Nome'):
    text = re.sub(r'\s+', ' ', str(value or ''))
    text = _strip_control(text).strip()
    if not text:
        raise LibraryError(f'{label} non valido')
    return text[:max_len].strip()


def clean_optional(value, max_len):
    text = _strip_control(re.sub(r'\s+', ' ', str(value or ''))).strip()
    return text[:max_len].strip() or None


def clean_color(value):
    if value in (None, '', 'none'):
        return None
    if value not in COLOR_KEYS:
        raise LibraryError('Colore non valido')
    return value


def clean_tag_name(value):
    text = _strip_control(re.sub(r'\s+', ' ', str(value or ''))).strip()
    text = text.lstrip('#').strip().strip(' .,;:!?"\'`')
    return text[:MAX_TAG_NAME].strip()


def _natural_key(name):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', name)]


def tag_key(name):
    """Case and accent insensitive key: 'Città' and 'citta' are the same tag."""
    text = unicodedata.normalize('NFKD', name.casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', text).strip()


def auto_tag_color(key):
    return TAG_AUTO_COLORS[zlib.crc32(key.encode('utf-8')) % len(TAG_AUTO_COLORS)]


# ---------------------------------------------------------------------------
# Libraries (one private library per browser, shared via an access code)
# ---------------------------------------------------------------------------

def generate_code():
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw):
    if not raw:
        return None
    code = re.sub(r'[^0-9A-Za-z]', '', str(raw)).upper()
    code = code.translate(str.maketrans('OIL', '011'))
    if len(code) != CODE_LENGTH or any(c not in CODE_ALPHABET for c in code):
        return None
    return code


def format_code(code):
    return '-'.join(code[i:i + 4] for i in range(0, len(code), 4))


def create_library():
    library_id = uuid.uuid4().hex
    now = time.time()
    for _ in range(5):
        code = generate_code()
        try:
            with _write() as conn:
                conn.execute(
                    'INSERT INTO libraries (id, code, created_at, last_seen_at) VALUES (?, ?, ?, ?)',
                    (library_id, code, now, now),
                )
            return {'id': library_id, 'code': code, 'created_at': now, 'last_seen_at': now}
        except sqlite3.IntegrityError:
            continue
    raise LibraryError('Impossibile creare la libreria', 500)


def get_library_by_code(code):
    code = normalize_code(code)
    if not code:
        return None
    with _read() as conn:
        row = conn.execute('SELECT * FROM libraries WHERE code = ?', (code,)).fetchone()
    return dict(row) if row else None


def touch_library(library):
    """Record activity at most once per hour to avoid a write per request."""
    now = time.time()
    if now - (library.get('last_seen_at') or 0) < 3600:
        return
    with _write() as conn:
        conn.execute('UPDATE libraries SET last_seen_at = ? WHERE id = ?', (now, library['id']))


def rotate_code(library_id):
    for _ in range(5):
        code = generate_code()
        try:
            with _write() as conn:
                conn.execute('UPDATE libraries SET code = ? WHERE id = ?', (code, library_id))
            return code
        except sqlite3.IntegrityError:
            continue
    raise LibraryError('Impossibile generare un nuovo codice', 500)


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

FOLDER_COLS = 'id, parent_id, name, color, position, created_at, updated_at'


def _folder_dict(row):
    return {
        'id': row['id'],
        'parent_id': row['parent_id'],
        'name': row['name'],
        'color': row['color'],
        'position': row['position'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def _active_folder(conn, library_id, folder_id):
    row = conn.execute(
        'SELECT * FROM folders WHERE id = ? AND library_id = ? AND deleted_at IS NULL',
        (folder_id, library_id),
    ).fetchone()
    if not row:
        raise NotFound('Cartella non trovata')
    return row


def _folder_ancestors(conn, folder_id):
    """Ids from folder_id up to the root (inclusive)."""
    chain = []
    current = folder_id
    while current and current not in chain and len(chain) < 1000:
        chain.append(current)
        row = conn.execute('SELECT parent_id FROM folders WHERE id = ?', (current,)).fetchone()
        current = row['parent_id'] if row else None
    return chain


def _subtree_folder_ids(conn, library_id, folder_id, include_deleted=False):
    deleted_clause = '' if include_deleted else 'AND f.deleted_at IS NULL'
    rows = conn.execute(
        f"""
        WITH RECURSIVE sub(id) AS (
            SELECT id FROM folders WHERE id = ? AND library_id = ?
            UNION
            SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id
            WHERE f.library_id = ? {deleted_clause}
        )
        SELECT id FROM sub
        """,
        (folder_id, library_id, library_id),
    ).fetchall()
    return [r['id'] for r in rows]


def _subtree_height(conn, library_id, folder_id):
    row = conn.execute(
        """
        WITH RECURSIVE sub(id, depth) AS (
            SELECT ?, 1
            UNION
            SELECT f.id, sub.depth + 1 FROM folders f JOIN sub ON f.parent_id = sub.id
            WHERE f.library_id = ? AND f.deleted_at IS NULL AND sub.depth < 200
        )
        SELECT MAX(depth) FROM sub
        """,
        (folder_id, library_id),
    ).fetchone()
    return row[0] or 1


def _top_positions(conn, table, parent_col, library_id, parent_id, count):
    """Positions that place `count` items (in order) above existing siblings."""
    row = conn.execute(
        f'SELECT MIN(position) FROM {table} '
        f'WHERE library_id = ? AND {parent_col} IS ? AND deleted_at IS NULL',
        (library_id, parent_id),
    ).fetchone()
    if row[0] is None:
        return [1024.0 * (i + 1) for i in range(count)]
    return [row[0] - 1024.0 * (count - i) for i in range(count)]


def list_folders(library_id):
    with _read() as conn:
        rows = conn.execute(
            f'SELECT {FOLDER_COLS} FROM folders WHERE library_id = ? AND deleted_at IS NULL '
            'ORDER BY position, created_at',
            (library_id,),
        ).fetchall()
    return [_folder_dict(r) for r in rows]


def get_folder(library_id, folder_id):
    with _read() as conn:
        return _folder_dict(_active_folder(conn, library_id, folder_id))


def create_folder(library_id, name, parent_id=None, color=None):
    name = clean_name(name, MAX_FOLDER_NAME, 'Nome cartella')
    color = clean_color(color)
    parent_id = parent_id or None
    now = time.time()
    folder_id = uuid.uuid4().hex
    with _write() as conn:
        if parent_id:
            _active_folder(conn, library_id, parent_id)
            if len(_folder_ancestors(conn, parent_id)) >= MAX_FOLDER_DEPTH:
                raise LibraryError('Hai raggiunto il numero massimo di sottocartelle')
        position = _top_positions(conn, 'folders', 'parent_id', library_id, parent_id, 1)[0]
        conn.execute(
            'INSERT INTO folders (id, library_id, parent_id, name, color, position, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (folder_id, library_id, parent_id, name, color, position, now, now),
        )
        row = conn.execute(f'SELECT {FOLDER_COLS} FROM folders WHERE id = ?', (folder_id,)).fetchone()
    return _folder_dict(row)


def update_folder(library_id, folder_id, name=_UNSET, color=_UNSET):
    with _write() as conn:
        _active_folder(conn, library_id, folder_id)
        sets, params = [], []
        if name is not _UNSET:
            sets.append('name = ?')
            params.append(clean_name(name, MAX_FOLDER_NAME, 'Nome cartella'))
        if color is not _UNSET:
            sets.append('color = ?')
            params.append(clean_color(color))
        if sets:
            sets.append('updated_at = ?')
            params.append(time.time())
            conn.execute(f"UPDATE folders SET {', '.join(sets)} WHERE id = ?", (*params, folder_id))
        row = conn.execute(f'SELECT {FOLDER_COLS} FROM folders WHERE id = ?', (folder_id,)).fetchone()
    return _folder_dict(row)


def ensure_folder_paths(library_id, parent_id, paths):
    """
    Create (or reuse, matching names case-insensitively) the folder hierarchy
    described by relative directory paths such as "Marketing/Social".
    Used when a whole folder is uploaded: uploading the same folder twice
    merges into the existing one instead of creating a copy.

    Returns ({original_path: folder_id} for every path prefix, [created ids]).
    """
    parent_id = parent_id or None
    requested = []
    for raw in paths or []:
        raw_parts = [p for p in str(raw).replace('\\', '/').split('/') if p.strip() not in ('', '.', '..')]
        if not raw_parts:
            continue
        clean_parts = tuple(clean_name(p, MAX_FOLDER_NAME, 'Nome cartella') for p in raw_parts)
        for i in range(1, len(raw_parts) + 1):
            requested.append(('/'.join(raw_parts[:i]), clean_parts[:i]))

    unique_paths = {parts for _, parts in requested}
    if len(unique_paths) > 1000:
        raise LibraryError('Troppe cartelle in un solo caricamento (massimo 1000)')

    resolved = {}
    created = []
    new_by_parent = defaultdict(list)
    now = time.time()
    with _write() as conn:
        base_depth = 0
        if parent_id:
            _active_folder(conn, library_id, parent_id)
            base_depth = len(_folder_ancestors(conn, parent_id))
        for parts in sorted(unique_paths, key=len):
            if base_depth + len(parts) > MAX_FOLDER_DEPTH:
                raise LibraryError('La cartella ha troppi livelli di sottocartelle')
            parent = resolved[parts[:-1]] if len(parts) > 1 else parent_id
            name = parts[-1]
            existing = conn.execute(
                'SELECT id FROM folders WHERE library_id = ? AND parent_id IS ? AND deleted_at IS NULL '
                'AND name = ? COLLATE NOCASE ORDER BY position LIMIT 1',
                (library_id, parent, name),
            ).fetchone()
            if existing:
                resolved[parts] = existing['id']
                continue
            folder_id = uuid.uuid4().hex
            position = _top_positions(conn, 'folders', 'parent_id', library_id, parent, 1)[0]
            conn.execute(
                'INSERT INTO folders (id, library_id, parent_id, name, color, position, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, NULL, ?, ?, ?)',
                (folder_id, library_id, parent, name, position, now, now),
            )
            resolved[parts] = folder_id
            created.append(folder_id)
            new_by_parent[parent].append((name, folder_id))

        # New sibling folders keep the (natural) alphabetical order of the uploaded folder
        for parent, children in new_by_parent.items():
            children.sort(key=lambda child: _natural_key(child[0]))
            ids = [folder_id for _, folder_id in children]
            row = conn.execute(
                f'SELECT MIN(position) FROM folders WHERE library_id = ? AND parent_id IS ? '
                f'AND deleted_at IS NULL AND id NOT IN ({_ph(ids)})',
                (library_id, parent, *ids),
            ).fetchone()
            if row[0] is None:
                positions = [1024.0 * (i + 1) for i in range(len(ids))]
            else:
                positions = [row[0] - 1024.0 * (len(ids) - i) for i in range(len(ids))]
            for folder_id, position in zip(ids, positions):
                conn.execute('UPDATE folders SET position = ? WHERE id = ?', (position, folder_id))

    mapping = {raw: resolved[parts] for raw, parts in requested}
    return mapping, created


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------

TRANSLATION_PUBLIC_COLS = (
    'id, book_id, source_lang, target_lang, provider, model, status, progress, '
    'status_text, error, output_filename, created_at, updated_at, completed_at'
)

_BOOK_INSERT_FIELDS = {
    'title', 'author', 'original_filename', 'file_type', 'file_size', 'file_path',
    'sha256', 'language', 'summary', 'num_pages', 'num_chapters', 'total_words',
    'total_chars', 'estimated_tokens', 'has_cover', 'color', 'favorite',
}


def _translation_public(row):
    return {
        'id': row['id'],
        'source_lang': row['source_lang'],
        'target_lang': row['target_lang'],
        'provider': row['provider'],
        'model': row['model'],
        'status': row['status'],
        'progress': row['progress'],
        'status_text': row['status_text'],
        'error': row['error'],
        'output_filename': row['output_filename'],
        'created_at': row['created_at'],
        'completed_at': row['completed_at'],
    }


def _book_dict(row, tags, translations):
    return {
        'id': row['id'],
        'folder_id': row['folder_id'],
        'title': row['title'],
        'author': row['author'],
        'original_filename': row['original_filename'],
        'file_type': row['file_type'],
        'file_size': row['file_size'],
        'color': row['color'],
        'favorite': bool(row['favorite']),
        'position': row['position'],
        'language': row['language'],
        'summary': row['summary'],
        'num_pages': row['num_pages'],
        'num_chapters': row['num_chapters'],
        'total_words': row['total_words'],
        'total_chars': row['total_chars'],
        'estimated_tokens': row['estimated_tokens'],
        'has_cover': bool(row['has_cover']),
        'tag_status': row['tag_status'],
        'tag_method': row['tag_method'],
        'tags': tags,
        'translations': translations,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def list_books(library_id):
    with _read() as conn:
        rows = conn.execute(
            'SELECT * FROM books WHERE library_id = ? AND deleted_at IS NULL ORDER BY position, created_at',
            (library_id,),
        ).fetchall()
        tag_rows = conn.execute(
            'SELECT bt.book_id, bt.tag_id, bt.source FROM book_tags bt '
            'JOIN books b ON b.id = bt.book_id '
            'WHERE b.library_id = ? AND b.deleted_at IS NULL ORDER BY bt.created_at, bt.rowid',
            (library_id,),
        ).fetchall()
        translation_rows = conn.execute(
            f'SELECT {TRANSLATION_PUBLIC_COLS} FROM translations '
            'WHERE library_id = ? AND book_id IS NOT NULL ORDER BY created_at',
            (library_id,),
        ).fetchall()

    tags = defaultdict(list)
    for r in tag_rows:
        tags[r['book_id']].append({'id': r['tag_id'], 'source': r['source']})
    translations = defaultdict(list)
    for r in translation_rows:
        translations[r['book_id']].append(_translation_public(r))
    return [_book_dict(r, tags[r['id']], translations[r['id']]) for r in rows]


def _load_book(conn, library_id, book_id, include_deleted=False):
    deleted_clause = '' if include_deleted else 'AND deleted_at IS NULL'
    return conn.execute(
        f'SELECT * FROM books WHERE id = ? AND library_id = ? {deleted_clause}',
        (book_id, library_id),
    ).fetchone()


def get_book(library_id, book_id, include_deleted=False):
    """Public representation of a book (None if missing)."""
    with _read() as conn:
        row = _load_book(conn, library_id, book_id, include_deleted)
        if not row:
            return None
        tag_rows = conn.execute(
            'SELECT tag_id, source FROM book_tags WHERE book_id = ? ORDER BY created_at, rowid', (book_id,)
        ).fetchall()
        translation_rows = conn.execute(
            f'SELECT {TRANSLATION_PUBLIC_COLS} FROM translations WHERE book_id = ? ORDER BY created_at',
            (book_id,),
        ).fetchall()
    return _book_dict(
        row,
        [{'id': r['tag_id'], 'source': r['source']} for r in tag_rows],
        [_translation_public(r) for r in translation_rows],
    )


def get_book_record(library_id, book_id, include_deleted=False):
    """Internal representation including file paths (None if missing)."""
    with _read() as conn:
        row = _load_book(conn, library_id, book_id, include_deleted)
    return dict(row) if row else None


def count_books(library_id):
    with _read() as conn:
        return conn.execute(
            'SELECT COUNT(*) FROM books WHERE library_id = ? AND deleted_at IS NULL', (library_id,)
        ).fetchone()[0]


def find_duplicate(library_id, sha256):
    with _read() as conn:
        row = conn.execute(
            'SELECT id FROM books WHERE library_id = ? AND sha256 = ? AND deleted_at IS NULL '
            'ORDER BY created_at LIMIT 1',
            (library_id, sha256),
        ).fetchone()
    return get_book(library_id, row['id']) if row else None


def create_book(library_id, book_id, folder_id=None, **fields):
    unknown = set(fields) - _BOOK_INSERT_FIELDS
    if unknown:
        raise ValueError(f"Unknown book fields: {unknown}")
    folder_id = folder_id or None
    now = time.time()
    with _write() as conn:
        if folder_id:
            _active_folder(conn, library_id, folder_id)
        position = _top_positions(conn, 'books', 'folder_id', library_id, folder_id, 1)[0]
        values = {
            'id': book_id,
            'library_id': library_id,
            'folder_id': folder_id,
            'position': position,
            'tag_status': 'pending',
            'tag_updated_at': now,
            'created_at': now,
            'updated_at': now,
            **fields,
        }
        columns = ', '.join(values)
        conn.execute(
            f'INSERT INTO books ({columns}) VALUES ({_ph(values)})',
            tuple(values.values()),
        )
    return get_book(library_id, book_id)


def _book_updates(title=_UNSET, author=_UNSET, color=_UNSET, favorite=_UNSET):
    sets, params = [], []
    if title is not _UNSET:
        sets.append('title = ?')
        params.append(clean_name(title, MAX_TITLE, 'Titolo'))
    if author is not _UNSET:
        sets.append('author = ?')
        params.append(clean_optional(author, MAX_AUTHOR))
    if color is not _UNSET:
        sets.append('color = ?')
        params.append(clean_color(color))
    if favorite is not _UNSET:
        sets.append('favorite = ?')
        params.append(1 if favorite else 0)
    return sets, params


def update_book(library_id, book_id, **changes):
    sets, params = _book_updates(**changes)
    with _write() as conn:
        if not _load_book(conn, library_id, book_id):
            raise NotFound('Libro non trovato')
        if sets:
            conn.execute(
                f"UPDATE books SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                (*params, time.time(), book_id),
            )
    return get_book(library_id, book_id)


def update_books(library_id, book_ids, **changes):
    """Bulk change color / favorite. Only title-less fields make sense here."""
    sets, params = _book_updates(**changes)
    if not sets:
        return 0
    updated = 0
    with _write() as conn:
        for chunk in _chunked(dict.fromkeys(book_ids)):
            cur = conn.execute(
                f"UPDATE books SET {', '.join(sets)}, updated_at = ? "
                f'WHERE library_id = ? AND deleted_at IS NULL AND id IN ({_ph(chunk)})',
                (*params, time.time(), library_id, *chunk),
            )
            updated += cur.rowcount
    return updated


def set_tag_status(book_id, status, error=None):
    with _write() as conn:
        conn.execute(
            'UPDATE books SET tag_status = ?, tag_error = ?, tag_updated_at = ? WHERE id = ?',
            (status, error, time.time(), book_id),
        )


def claim_stale_tagging(library_id, older_than=300):
    """Books whose tagging never finished (e.g. server restart). Marks them as re-queued."""
    now = time.time()
    with _write() as conn:
        rows = conn.execute(
            "SELECT id FROM books WHERE library_id = ? AND deleted_at IS NULL "
            "AND tag_status IN ('pending', 'running') AND COALESCE(tag_updated_at, created_at) < ?",
            (library_id, now - older_than),
        ).fetchall()
        ids = [r['id'] for r in rows]
        for chunk in _chunked(ids):
            conn.execute(
                f"UPDATE books SET tag_status = 'pending', tag_updated_at = ? WHERE id IN ({_ph(chunk)})",
                (now, *chunk),
            )
    return ids


def activity(library_id):
    """Things still in progress: tagging and translations."""
    with _read() as conn:
        tagging = [
            r['id'] for r in conn.execute(
                "SELECT id FROM books WHERE library_id = ? AND deleted_at IS NULL "
                "AND tag_status IN ('pending', 'running')",
                (library_id,),
            )
        ]
        translations = [
            dict(r) for r in conn.execute(
                "SELECT id, book_id, status, progress, status_text FROM translations "
                "WHERE library_id = ? AND book_id IS NOT NULL AND status IN ('pending', 'processing')",
                (library_id,),
            )
        ]
    return {'tagging': tagging, 'translations': translations}


# ---------------------------------------------------------------------------
# Moving & ordering
# ---------------------------------------------------------------------------

def move_items(library_id, book_ids=(), folder_ids=(), target_id=None):
    """Move books and folders into target_id (None = library root), placing them on top."""
    target_id = target_id or None
    now = time.time()
    with _write() as conn:
        ancestors = []
        if target_id:
            _active_folder(conn, library_id, target_id)
            ancestors = _folder_ancestors(conn, target_id)

        folders = []
        for folder_id in dict.fromkeys(folder_ids):
            row = _active_folder(conn, library_id, folder_id)
            if folder_id in ancestors:
                raise LibraryError(
                    'Non puoi spostare una cartella dentro se stessa o in una sua sottocartella',
                    400, 'cycle',
                )
            if row['parent_id'] == target_id:
                continue
            if len(ancestors) + _subtree_height(conn, library_id, folder_id) > MAX_FOLDER_DEPTH:
                raise LibraryError('Troppi livelli di sottocartelle')
            folders.append(folder_id)

        books = []
        for book_id in dict.fromkeys(book_ids):
            row = _load_book(conn, library_id, book_id)
            if not row:
                raise NotFound('Libro non trovato')
            if row['folder_id'] == target_id:
                continue
            books.append(book_id)

        if folders:
            positions = _top_positions(conn, 'folders', 'parent_id', library_id, target_id, len(folders))
            for folder_id, position in zip(folders, positions):
                conn.execute(
                    'UPDATE folders SET parent_id = ?, position = ?, updated_at = ? WHERE id = ?',
                    (target_id, position, now, folder_id),
                )
        if books:
            positions = _top_positions(conn, 'books', 'folder_id', library_id, target_id, len(books))
            for book_id, position in zip(books, positions):
                conn.execute(
                    'UPDATE books SET folder_id = ?, position = ?, updated_at = ? WHERE id = ?',
                    (target_id, position, now, book_id),
                )
    return {'books': len(books), 'folders': len(folders)}


def reorder(library_id, kind, parent_id, ordered_ids):
    """Set the manual order of the books (or folders) inside one container."""
    if kind == 'book':
        table, parent_col = 'books', 'folder_id'
    elif kind == 'folder':
        table, parent_col = 'folders', 'parent_id'
    else:
        raise LibraryError('Tipo non valido')
    parent_id = parent_id or None
    with _write() as conn:
        if parent_id:
            _active_folder(conn, library_id, parent_id)
        rows = conn.execute(
            f'SELECT id FROM {table} WHERE library_id = ? AND {parent_col} IS ? AND deleted_at IS NULL '
            'ORDER BY position, created_at',
            (library_id, parent_id),
        ).fetchall()
        current = [r['id'] for r in rows]
        members = set(current)
        new_order = [i for i in dict.fromkeys(ordered_ids) if i in members]
        listed = set(new_order)
        new_order += [i for i in current if i not in listed]
        for index, item_id in enumerate(new_order):
            conn.execute(
                f'UPDATE {table} SET position = ? WHERE id = ?',
                (1024.0 * (index + 1), item_id),
            )
    return len(new_order)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def _tag_dict(row, count=0):
    return {'id': row['id'], 'name': row['name'], 'color': row['color'], 'count': count}


def _get_or_create_tag(conn, library_id, name):
    name = clean_tag_name(name)
    if not name:
        return None
    key = tag_key(name)
    if not key:
        return None
    row = conn.execute(
        'SELECT * FROM tags WHERE library_id = ? AND name_key = ?', (library_id, key)
    ).fetchone()
    if row:
        return row
    tag_id = uuid.uuid4().hex
    conn.execute(
        'INSERT INTO tags (id, library_id, name, name_key, color, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        (tag_id, library_id, name, key, auto_tag_color(key), time.time()),
    )
    return conn.execute('SELECT * FROM tags WHERE id = ?', (tag_id,)).fetchone()


def _delete_orphan_tags(conn, library_id):
    conn.execute(
        'DELETE FROM tags WHERE library_id = ? '
        'AND NOT EXISTS (SELECT 1 FROM book_tags bt WHERE bt.tag_id = tags.id)',
        (library_id,),
    )


def _active_book_ids(conn, library_id, book_ids):
    found = []
    for chunk in _chunked(dict.fromkeys(book_ids)):
        found += [
            r['id'] for r in conn.execute(
                f'SELECT id FROM books WHERE library_id = ? AND deleted_at IS NULL AND id IN ({_ph(chunk)})',
                (library_id, *chunk),
            )
        ]
    return found


def list_tags(library_id):
    with _read() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.name, t.color, COUNT(b.id) AS count
            FROM tags t
            LEFT JOIN book_tags bt ON bt.tag_id = t.id
            LEFT JOIN books b ON b.id = bt.book_id AND b.deleted_at IS NULL
            WHERE t.library_id = ?
            GROUP BY t.id
            ORDER BY count DESC, t.name COLLATE NOCASE
            """,
            (library_id,),
        ).fetchall()
    return [_tag_dict(r, r['count']) for r in rows]


def tag_vocabulary(library_id, limit=120):
    """Existing tag names, most used first (fed to the AI to keep tags consistent)."""
    with _read() as conn:
        rows = conn.execute(
            'SELECT t.name, COUNT(bt.book_id) AS n FROM tags t '
            'LEFT JOIN book_tags bt ON bt.tag_id = t.id '
            'WHERE t.library_id = ? GROUP BY t.id ORDER BY n DESC, t.name COLLATE NOCASE LIMIT ?',
            (library_id, limit),
        ).fetchall()
    return [r['name'] for r in rows]


def add_tags(library_id, book_ids, names, source='manual'):
    now = time.time()
    with _write() as conn:
        ids = _active_book_ids(conn, library_id, book_ids)
        tags = [t for t in (_get_or_create_tag(conn, library_id, n) for n in names) if t]
        for book_id in ids:
            for tag in tags:
                if source == 'manual':
                    # A manual add "confirms" a tag the AI had suggested
                    conn.execute(
                        "INSERT INTO book_tags (book_id, tag_id, source, created_at) VALUES (?, ?, 'manual', ?) "
                        "ON CONFLICT(book_id, tag_id) DO UPDATE SET source = 'manual'",
                        (book_id, tag['id'], now),
                    )
                else:
                    conn.execute(
                        'INSERT INTO book_tags (book_id, tag_id, source, created_at) VALUES (?, ?, ?, ?) '
                        'ON CONFLICT(book_id, tag_id) DO NOTHING',
                        (book_id, tag['id'], source, now),
                    )
        _delete_orphan_tags(conn, library_id)
    return [_tag_dict(t) for t in tags]


def remove_tags(library_id, book_ids, tag_ids):
    with _write() as conn:
        ids = _active_book_ids(conn, library_id, book_ids)
        valid_tags = []
        for chunk in _chunked(dict.fromkeys(tag_ids)):
            valid_tags += [
                r['id'] for r in conn.execute(
                    f'SELECT id FROM tags WHERE library_id = ? AND id IN ({_ph(chunk)})',
                    (library_id, *chunk),
                )
            ]
        for book_chunk in _chunked(ids):
            for tag_chunk in _chunked(valid_tags):
                conn.execute(
                    f'DELETE FROM book_tags WHERE book_id IN ({_ph(book_chunk)}) AND tag_id IN ({_ph(tag_chunk)})',
                    (*book_chunk, *tag_chunk),
                )
        _delete_orphan_tags(conn, library_id)


def apply_tagging(library_id, book_id, tags, language=None, summary=None, method=None):
    """Replace the AI/automatic tags of a book (manual tags are kept)."""
    now = time.time()
    with _write() as conn:
        if not _load_book(conn, library_id, book_id, include_deleted=True):
            return
        conn.execute("DELETE FROM book_tags WHERE book_id = ? AND source = 'auto'", (book_id,))
        for index, name in enumerate(tags):
            tag = _get_or_create_tag(conn, library_id, name)
            if tag:
                # Microsecond offsets keep the AI's relevance order
                conn.execute(
                    "INSERT INTO book_tags (book_id, tag_id, source, created_at) VALUES (?, ?, 'auto', ?) "
                    'ON CONFLICT(book_id, tag_id) DO NOTHING',
                    (book_id, tag['id'], now + index * 1e-6),
                )
        sets = ["tag_status = 'done'", 'tag_method = ?', 'tag_error = NULL', 'tag_updated_at = ?']
        params = [method, now]
        if language:
            sets.append('language = ?')
            params.append(str(language)[:40])
        if summary:
            sets.append('summary = ?')
            params.append(str(summary)[:600])
        conn.execute(f"UPDATE books SET {', '.join(sets)} WHERE id = ?", (*params, book_id))
        _delete_orphan_tags(conn, library_id)


def update_tag(library_id, tag_id, name=_UNSET, color=_UNSET):
    """Rename / recolor a tag. Renaming onto an existing tag merges the two."""
    with _write() as conn:
        row = conn.execute(
            'SELECT * FROM tags WHERE id = ? AND library_id = ?', (tag_id, library_id)
        ).fetchone()
        if not row:
            raise NotFound('Tag non trovato')
        result_id = tag_id
        if name is not _UNSET:
            new_name = clean_tag_name(name)
            if not new_name:
                raise LibraryError('Nome tag non valido')
            key = tag_key(new_name)
            other = conn.execute(
                'SELECT * FROM tags WHERE library_id = ? AND name_key = ? AND id != ?',
                (library_id, key, tag_id),
            ).fetchone()
            if other:
                conn.execute(
                    'INSERT OR IGNORE INTO book_tags (book_id, tag_id, source, created_at) '
                    'SELECT book_id, ?, source, created_at FROM book_tags WHERE tag_id = ?',
                    (other['id'], tag_id),
                )
                conn.execute('DELETE FROM tags WHERE id = ?', (tag_id,))
                result_id = other['id']
            else:
                conn.execute(
                    'UPDATE tags SET name = ?, name_key = ? WHERE id = ?', (new_name, key, tag_id)
                )
        if color is not _UNSET:
            color_value = clean_color(color)
            current = conn.execute('SELECT name_key FROM tags WHERE id = ?', (result_id,)).fetchone()
            conn.execute(
                'UPDATE tags SET color = ? WHERE id = ?',
                (color_value or auto_tag_color(current['name_key']), result_id),
            )
        final = conn.execute('SELECT * FROM tags WHERE id = ?', (result_id,)).fetchone()
    return {**_tag_dict(final), 'merged': result_id != tag_id}


def delete_tag(library_id, tag_id):
    with _write() as conn:
        cur = conn.execute('DELETE FROM tags WHERE id = ? AND library_id = ?', (tag_id, library_id))
        if not cur.rowcount:
            raise NotFound('Tag non trovato')


# ---------------------------------------------------------------------------
# Trash
# ---------------------------------------------------------------------------

def trash_items(library_id, book_ids=(), folder_ids=()):
    """Soft delete. Folders take their whole content with them. Returns an undo id."""
    trash_id = uuid.uuid4().hex
    now = time.time()
    counts = {'books': 0, 'folders': 0}
    with _write() as conn:
        for folder_id in dict.fromkeys(folder_ids):
            active = conn.execute(
                'SELECT 1 FROM folders WHERE id = ? AND library_id = ? AND deleted_at IS NULL',
                (folder_id, library_id),
            ).fetchone()
            if not active:
                continue
            subtree = _subtree_folder_ids(conn, library_id, folder_id)
            for chunk in _chunked(subtree):
                conn.execute(
                    f'UPDATE folders SET deleted_at = ?, trash_id = ? WHERE id IN ({_ph(chunk)})',
                    (now, trash_id, *chunk),
                )
                cur = conn.execute(
                    'UPDATE books SET deleted_at = ?, trash_id = ? '
                    f'WHERE library_id = ? AND deleted_at IS NULL AND folder_id IN ({_ph(chunk)})',
                    (now, trash_id, library_id, *chunk),
                )
                counts['books'] += cur.rowcount
            counts['folders'] += len(subtree)
        for chunk in _chunked(dict.fromkeys(book_ids)):
            cur = conn.execute(
                'UPDATE books SET deleted_at = ?, trash_id = ? '
                f'WHERE library_id = ? AND deleted_at IS NULL AND id IN ({_ph(chunk)})',
                (now, trash_id, library_id, *chunk),
            )
            counts['books'] += cur.rowcount
    return {'trash_id': trash_id, **counts}


def _reattach_orphans(conn, folder_ids, book_ids):
    """Restored items whose parent is gone (or still in the trash) go to the root."""
    for chunk in _chunked(folder_ids):
        conn.execute(
            f'UPDATE folders SET parent_id = NULL WHERE id IN ({_ph(chunk)}) AND parent_id IS NOT NULL '
            'AND NOT EXISTS (SELECT 1 FROM folders p WHERE p.id = folders.parent_id AND p.deleted_at IS NULL)',
            chunk,
        )
    for chunk in _chunked(book_ids):
        conn.execute(
            f'UPDATE books SET folder_id = NULL WHERE id IN ({_ph(chunk)}) AND folder_id IS NOT NULL '
            'AND NOT EXISTS (SELECT 1 FROM folders f WHERE f.id = books.folder_id AND f.deleted_at IS NULL)',
            chunk,
        )


def restore_trash(library_id, trash_id):
    """Undo one delete operation."""
    with _write() as conn:
        folder_ids = [
            r['id'] for r in conn.execute(
                'SELECT id FROM folders WHERE library_id = ? AND trash_id = ? AND deleted_at IS NOT NULL',
                (library_id, trash_id),
            )
        ]
        book_ids = [
            r['id'] for r in conn.execute(
                'SELECT id FROM books WHERE library_id = ? AND trash_id = ? AND deleted_at IS NOT NULL',
                (library_id, trash_id),
            )
        ]
        conn.execute(
            'UPDATE folders SET deleted_at = NULL, trash_id = NULL WHERE library_id = ? AND trash_id = ?',
            (library_id, trash_id),
        )
        conn.execute(
            'UPDATE books SET deleted_at = NULL, trash_id = NULL WHERE library_id = ? AND trash_id = ?',
            (library_id, trash_id),
        )
        _reattach_orphans(conn, folder_ids, book_ids)
    return {'books': len(book_ids), 'folders': len(folder_ids)}


def restore_items(library_id, book_ids=(), folder_ids=()):
    """Restore items picked from the trash (a folder comes back with its content)."""
    restored_folders, restored_books = [], []
    with _write() as conn:
        for folder_id in dict.fromkeys(folder_ids):
            row = conn.execute(
                'SELECT trash_id FROM folders WHERE id = ? AND library_id = ? AND deleted_at IS NOT NULL',
                (folder_id, library_id),
            ).fetchone()
            if not row:
                continue
            subtree = _subtree_folder_ids(conn, library_id, folder_id, include_deleted=True)
            for chunk in _chunked(subtree):
                ids = [
                    r['id'] for r in conn.execute(
                        f'SELECT id FROM folders WHERE id IN ({_ph(chunk)}) AND trash_id IS ? AND deleted_at IS NOT NULL',
                        (*chunk, row['trash_id']),
                    )
                ]
                for part in _chunked(ids):
                    conn.execute(
                        f'UPDATE folders SET deleted_at = NULL, trash_id = NULL WHERE id IN ({_ph(part)})', part
                    )
                restored_folders += ids
                book_rows = [
                    r['id'] for r in conn.execute(
                        f'SELECT id FROM books WHERE folder_id IN ({_ph(chunk)}) AND trash_id IS ? '
                        'AND deleted_at IS NOT NULL',
                        (*chunk, row['trash_id']),
                    )
                ]
                for part in _chunked(book_rows):
                    conn.execute(
                        f'UPDATE books SET deleted_at = NULL, trash_id = NULL WHERE id IN ({_ph(part)})', part
                    )
                restored_books += book_rows
        for chunk in _chunked(dict.fromkeys(book_ids)):
            ids = [
                r['id'] for r in conn.execute(
                    f'SELECT id FROM books WHERE library_id = ? AND deleted_at IS NOT NULL AND id IN ({_ph(chunk)})',
                    (library_id, *chunk),
                )
            ]
            for part in _chunked(ids):
                conn.execute(
                    f'UPDATE books SET deleted_at = NULL, trash_id = NULL WHERE id IN ({_ph(part)})', part
                )
            restored_books += ids
        _reattach_orphans(conn, restored_folders, restored_books)
    return {'books': len(restored_books), 'folders': len(restored_folders)}


def list_trash(library_id):
    """Top-level trashed items (content of a trashed folder is shown through the folder)."""
    with _read() as conn:
        folders = conn.execute(
            'SELECT id, parent_id, name, color, deleted_at, trash_id FROM folders WHERE library_id = ?',
            (library_id,),
        ).fetchall()
        books = conn.execute(
            'SELECT id, folder_id, title, author, file_type, has_cover, created_at, deleted_at, trash_id '
            'FROM books WHERE library_id = ? AND deleted_at IS NOT NULL',
            (library_id,),
        ).fetchall()

    by_id = {f['id']: f for f in folders}
    children = defaultdict(list)
    for f in folders:
        children[f['parent_id']].append(f)
    books_by_folder = defaultdict(list)
    for b in books:
        books_by_folder[b['folder_id']].append(b)

    def is_deleted(folder_id):
        folder = by_id.get(folder_id)
        return bool(folder and folder['deleted_at'])

    def location(folder_id):
        names = []
        current = by_id.get(folder_id)
        while current and len(names) < 50:
            names.insert(0, current['name'])
            current = by_id.get(current['parent_id'])
        return names

    def batch_book_count(folder):
        total, stack = 0, [folder]
        while stack:
            current = stack.pop()
            total += sum(1 for b in books_by_folder[current['id']] if b['trash_id'] == folder['trash_id'])
            stack += [c for c in children[current['id']] if c['trash_id'] == folder['trash_id']]
        return total

    items = []
    for f in folders:
        if not f['deleted_at'] or is_deleted(f['parent_id']):
            continue
        items.append({
            'kind': 'folder', 'id': f['id'], 'name': f['name'], 'color': f['color'],
            'deleted_at': f['deleted_at'], 'location': location(f['parent_id']),
            'book_count': batch_book_count(f),
        })
    for b in books:
        if is_deleted(b['folder_id']):
            continue
        items.append({
            'kind': 'book', 'id': b['id'], 'name': b['title'], 'author': b['author'],
            'file_type': b['file_type'], 'has_cover': bool(b['has_cover']),
            'created_at': b['created_at'], 'deleted_at': b['deleted_at'],
            'location': location(b['folder_id']),
        })
    items.sort(key=lambda item: -item['deleted_at'])
    return items


def trash_count(library_id):
    with _read() as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM folders f WHERE f.library_id = ? AND f.deleted_at IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM folders p WHERE p.id = f.parent_id AND p.deleted_at IS NOT NULL))
            + (SELECT COUNT(*) FROM books b WHERE b.library_id = ? AND b.deleted_at IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM folders p WHERE p.id = b.folder_id AND p.deleted_at IS NOT NULL))
            """,
            (library_id, library_id),
        ).fetchone()
    return row[0]


def _purge_books(conn, library_id, book_ids):
    files = []
    for chunk in _chunked(book_ids):
        for r in conn.execute(
            f'SELECT id, file_path, has_cover FROM books WHERE id IN ({_ph(chunk)})', chunk
        ):
            files.append(r['file_path'])
            if r['has_cover']:
                files.append(cover_rel(library_id, r['id']))
        for r in conn.execute(
            f'SELECT output_path FROM translations WHERE book_id IN ({_ph(chunk)})', chunk
        ):
            if r['output_path']:
                files.append(r['output_path'])
        conn.execute(f'DELETE FROM books WHERE id IN ({_ph(chunk)})', chunk)
    return files


def purge_items(library_id, book_ids=(), folder_ids=()):
    """Permanently delete trashed items and their files."""
    with _write() as conn:
        folder_set = []
        for folder_id in dict.fromkeys(folder_ids):
            row = conn.execute(
                'SELECT 1 FROM folders WHERE id = ? AND library_id = ? AND deleted_at IS NOT NULL',
                (folder_id, library_id),
            ).fetchone()
            if row:
                folder_set += _subtree_folder_ids(conn, library_id, folder_id, include_deleted=True)
        folder_set = list(dict.fromkeys(folder_set))

        book_set = []
        for chunk in _chunked(folder_set):
            book_set += [
                r['id'] for r in conn.execute(
                    f'SELECT id FROM books WHERE library_id = ? AND deleted_at IS NOT NULL '
                    f'AND folder_id IN ({_ph(chunk)})',
                    (library_id, *chunk),
                )
            ]
        for chunk in _chunked(dict.fromkeys(book_ids)):
            book_set += [
                r['id'] for r in conn.execute(
                    f'SELECT id FROM books WHERE library_id = ? AND deleted_at IS NOT NULL AND id IN ({_ph(chunk)})',
                    (library_id, *chunk),
                )
            ]
        book_set = list(dict.fromkeys(book_set))

        files = _purge_books(conn, library_id, book_set)
        for chunk in _chunked(folder_set):
            conn.execute(f'DELETE FROM folders WHERE id IN ({_ph(chunk)})', chunk)
        _delete_orphan_tags(conn, library_id)
    remove_files(files)
    return {'books': len(book_set), 'folders': len(folder_set)}


def empty_trash(library_id):
    with _read() as conn:
        folder_ids = [
            r['id'] for r in conn.execute(
                'SELECT id FROM folders WHERE library_id = ? AND deleted_at IS NOT NULL', (library_id,)
            )
        ]
        book_ids = [
            r['id'] for r in conn.execute(
                'SELECT id FROM books WHERE library_id = ? AND deleted_at IS NOT NULL', (library_id,)
            )
        ]
    return purge_items(library_id, book_ids, folder_ids)


def purge_expired(library_id, retention=TRASH_RETENTION_SECONDS):
    cutoff = time.time() - retention
    with _read() as conn:
        folder_ids = [
            r['id'] for r in conn.execute(
                'SELECT id FROM folders WHERE library_id = ? AND deleted_at IS NOT NULL AND deleted_at < ?',
                (library_id, cutoff),
            )
        ]
        book_ids = [
            r['id'] for r in conn.execute(
                'SELECT id FROM books WHERE library_id = ? AND deleted_at IS NOT NULL AND deleted_at < ?',
                (library_id, cutoff),
            )
        ]
    if folder_ids or book_ids:
        return purge_items(library_id, book_ids, folder_ids)
    return {'books': 0, 'folders': 0}


# ---------------------------------------------------------------------------
# Translation jobs (persisted so every gunicorn worker can report progress)
# ---------------------------------------------------------------------------

_TRANSLATION_UPDATABLE = {
    'status', 'progress', 'status_text', 'error', 'output_filename', 'logs', 'logs_total',
}


def create_translation(translation_id, *, library_id=None, book_id=None, source_lang=None,
                       target_lang=None, provider=None, model=None, file_type=None,
                       input_path=None, output_path=None, status_text=None):
    now = time.time()
    with _write() as conn:
        conn.execute(
            'INSERT INTO translations (id, library_id, book_id, source_lang, target_lang, provider, model, '
            'file_type, input_path, output_path, status, progress, status_text, created_at, updated_at) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
            (translation_id, library_id, book_id, source_lang, target_lang, provider, model,
             file_type, input_path, output_path, status_text, now, now),
        )


def update_translation(translation_id, **fields):
    sets, params = [], []
    for key, value in fields.items():
        if key not in _TRANSLATION_UPDATABLE:
            raise ValueError(f"Unknown translation field: {key}")
        if key == 'logs':
            value = json.dumps(list(value)[-LOG_TAIL:], ensure_ascii=False)
        sets.append(f'{key} = ?')
        params.append(value)
    now = time.time()
    sets.append('updated_at = ?')
    params.append(now)
    if fields.get('status') == 'completed':
        sets.append('completed_at = COALESCE(completed_at, ?)')
        params.append(now)
    with _write() as conn:
        conn.execute(f"UPDATE translations SET {', '.join(sets)} WHERE id = ?", (*params, translation_id))


def _translation_row(row):
    data = dict(row)
    try:
        data['logs'] = json.loads(data.get('logs') or '[]')
    except ValueError:
        data['logs'] = []
    return data


def get_translation(translation_id):
    with _read() as conn:
        row = conn.execute('SELECT * FROM translations WHERE id = ?', (translation_id,)).fetchone()
    return _translation_row(row) if row else None


def get_library_translation(library_id, translation_id):
    with _read() as conn:
        row = conn.execute(
            'SELECT * FROM translations WHERE id = ? AND library_id = ?', (translation_id, library_id)
        ).fetchone()
    return _translation_row(row) if row else None


def delete_translation(library_id, translation_id):
    with _write() as conn:
        row = conn.execute(
            'SELECT * FROM translations WHERE id = ? AND library_id = ?', (translation_id, library_id)
        ).fetchone()
        if not row:
            raise NotFound('Traduzione non trovata')
        running = row['status'] in ('pending', 'processing')
        if running and time.time() - row['updated_at'] < STALE_TRANSLATION_SECONDS:
            raise LibraryError('La traduzione è ancora in corso', 409, 'running')
        conn.execute('DELETE FROM translations WHERE id = ?', (translation_id,))
    remove_files([row['output_path']])


def fail_stale_translations(max_idle=STALE_TRANSLATION_SECONDS):
    """Jobs whose worker died (restart / deploy) would otherwise look 'in progress' forever."""
    message = 'Traduzione interrotta (il server è stato riavviato). Riprova.'
    with _write() as conn:
        conn.execute(
            "UPDATE translations SET status = 'error', error = ?, status_text = ?, updated_at = ? "
            "WHERE status IN ('pending', 'processing') AND updated_at < ?",
            (message, message, time.time(), time.time() - max_idle),
        )
