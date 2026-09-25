"""
ULTIMATE TRANSLATOR - Library
Flask blueprint for the personal book library: the folders & tags page, its
REST API, uploads (single books or whole folders) and background auto-tagging.

On its own (branch claude/intelligent-clarke-0d4k7s) every browser gets a
private library identified by a secret code kept in an HttpOnly cookie.
In the production portal `libreria_ponte.py` plugs in through the hooks below:
the library is the one of the logged-in account, the code only lets
import_books.py in, and every book is also a book of the portal's library.
"""

import os
import re
import time
import uuid
import hashlib
import logging
import shutil
import threading
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, g, jsonify, redirect, render_template, request, send_file

import library_db
import tagger
from library_db import LibraryError, NotFound
from book_metadata import BookReadError, extract, title_from_filename

logger = logging.getLogger(__name__)

bp = Blueprint('library', __name__)

COOKIE_NAME = 'ut_library'
COOKIE_MAX_AGE = 10 * 365 * 24 * 3600
ALLOWED_EXTENSIONS = {'epub', 'pdf'}
MAX_BATCH = 5000

_ID_RE = re.compile(r'^[0-9a-f]{32}$')
_TRANSLATION_ID_RE = re.compile(r'^[0-9a-f-]{32,36}$')

# Tests switch this on to run tagging synchronously
TAGGING_INLINE = False

# Page address: the production portal already has its own /libreria and sets
# this before registering the blueprint
PAGE_PATH = os.getenv('LIBRARY_PAGE_PATH', '/libreria')

# Hooks for the production portal (None = standalone behaviour of the branch)
IDENTITY = None          # f(create) -> library dict or None (None = 401)
PAGE_ALLOWED = None      # f() -> True if the page may be shown, else redirect to /login
AFTER_IMPORT = None      # f(library_id, book_id) after a NEW book is stored
ON_MAINTENANCE = None    # f(library_id) at most once a minute
MIN_FREE_DISK_MB = int(os.getenv('MIN_FREE_DISK_MB', '500'))
_executor = None
_executor_lock = threading.Lock()
_last_maintenance = {}


class DuplicateBook(Exception):
    def __init__(self, book):
        super().__init__('duplicate book')
        self.book = book


# ---------------------------------------------------------------------------
# Library identity (cookie)
# ---------------------------------------------------------------------------

def current_library(create=True):
    cached = g.get('library')
    if cached:
        return cached
    if IDENTITY is not None:
        library = IDENTITY(create)
        if not library:
            raise LibraryError('Accesso richiesto: entra nel portale', 401, 'login_required')
        g.library = library
        return library
    code = request.cookies.get(COOKIE_NAME)
    library = library_db.get_library_by_code(code) if code else None
    if library:
        try:
            library_db.touch_library(library)
        except Exception as e:
            logger.debug(f"Could not update library activity: {e}")
    elif create:
        library = library_db.create_library()
        g.library_cookie = library['code']
    else:
        return None
    g.library = library
    return library


def current_library_id(create=True):
    library = current_library(create)
    return library['id'] if library else None


def writable_library_id():
    """
    The library a change applies to. Changes never create a library: with an unknown
    cookie (code changed on another device, data wiped) parallel uploads would otherwise
    scatter books into throwaway libraries. The page reloads its library and retries.
    """
    library = current_library(create=False)
    if not library:
        raise LibraryError('La libreria di questo browser non e piu disponibile: ricarica la pagina',
                           401, 'no_library')
    return library['id']


def _is_https():
    forwarded = request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip()
    return request.is_secure or forwarded == 'https'


@bp.after_app_request
def _store_library_cookie(response):
    code = g.pop('library_cookie', None)
    if code:
        response.set_cookie(
            COOKIE_NAME, code, max_age=COOKIE_MAX_AGE,
            httponly=True, samesite='Lax', secure=_is_https(),
        )
    return response


@bp.before_app_request
def _check_same_origin():
    """Reject cross-site writes to the library API (defense in depth next to SameSite)."""
    if request.method in ('POST', 'PATCH', 'PUT', 'DELETE') and request.path.startswith('/api/library'):
        origin = request.headers.get('Origin')
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({'error': 'Richiesta non consentita'}), 403
    return None


@bp.errorhandler(LibraryError)
def _library_error(error):
    payload = {'error': error.message}
    if error.code:
        payload['code'] = error.code
    return jsonify(payload), error.status


# ---------------------------------------------------------------------------
# Validation & serialization helpers
# ---------------------------------------------------------------------------

def _json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise LibraryError('Richiesta non valida')
    return data


def _id(value, label='elemento', optional=False):
    if value in (None, ''):
        if optional:
            return None
        raise LibraryError(f'Identificativo {label} mancante')
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise LibraryError(f'Identificativo {label} non valido')
    return value


def _ids(values, label='elemento'):
    if values in (None, ''):
        return []
    if not isinstance(values, list) or len(values) > MAX_BATCH:
        raise LibraryError('Elenco non valido')
    return [_id(v, label) for v in values]


def _translation_id(value):
    if not isinstance(value, str) or not _TRANSLATION_ID_RE.match(value):
        raise LibraryError('Identificativo traduzione non valido')
    return value


def cover_url(book_id, created_at):
    return f"/api/library/books/{book_id}/cover?v={int(created_at)}"


def serialize_book(book):
    data = dict(book)
    data['cover_url'] = cover_url(book['id'], book['created_at']) if book.get('has_cover') else None
    # the portal's reader (production): read, listen, annotate like any other book
    data['read_url'] = f"/leggi/{book['job_id']}" if book.get('job_id') else None
    return data


def download_name(title, ext):
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', title or '').strip().strip('.')
    base = re.sub(r'\s+', ' ', base)[:150].strip() or 'libro'
    return f"{base}.{ext}"


def _extension(filename):
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''


def _folder_label(library_id, folder_id):
    if not folder_id:
        return None
    folders = {f['id']: f for f in library_db.list_folders(library_id)}
    names = []
    current = folders.get(folder_id)
    while current and len(names) < 30:
        names.insert(0, current['name'])
        current = folders.get(current['parent_id'])
    return ' / '.join(names) or None


# ---------------------------------------------------------------------------
# Import & auto-tagging
# ---------------------------------------------------------------------------

def _free_pdf_cache():
    """MuPDF keeps decoded pages in a cache of up to 256 MB inside the web
    process. On the 1 GB production server, reading a hundred books in a row
    (covers, metadata) would keep that much memory taken from the reader and
    the voice: empty it after every book."""
    try:
        import fitz
        fitz.TOOLS.store_shrink(100)
    except Exception:
        pass


_JUNK_TITLE = re.compile(
    r'camscanner|microsoft word|untitled|preview of|\.(pdf|docx?|epub|indd|qxd)\b'
    r'|page \d+\s*-\s*\d+|^\s*document\s*\d*\s*$',
    re.IGNORECASE,
)


def clean_title(meta_title, filename):
    """
    The title stored in the file, unless it is junk left by a scanner or an
    editor ("CamScanner 07-02-2020", "Preview of “x.pdf”", "Letters_234x156.pdf,
    page 1-320") or another book's title in another alphabet: then the file
    name, which the user chose, is the better title.
    """
    fallback = title_from_filename(filename)
    title = (meta_title or '').strip()
    if not title or _JUNK_TITLE.search(title):
        return fallback
    latin_in_name = sum(c.isascii() and c.isalpha() for c in fallback)
    foreign_in_title = sum(ord(c) > 0x2FF for c in title)
    if latin_in_name >= 3 and foreign_in_title > len(title) * 0.3:
        return fallback
    return title


def _tagging_context(info):
    return {
        'description': info.get('description'),
        'keywords': info.get('keywords') or [],
        'toc': info.get('toc') or [],
        'text_sample': info.get('text_sample') or '',
        'language_code': info.get('language_code'),
    }


def import_book(library_id, file_storage, folder_id=None, allow_duplicate=False, reuse_duplicate=False):
    """
    Store an uploaded EPUB/PDF in the library, read its metadata and cover,
    and queue automatic tagging. Returns (book, was_existing_duplicate).
    """
    filename = os.path.basename((file_storage.filename or '').replace('\\', '/'))
    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        message = f'Formato non supportato: .{ext}. Usa EPUB o PDF.' if ext else 'Formato non supportato. Usa EPUB o PDF.'
        raise LibraryError(message, 400, 'unsupported')
    if folder_id:
        library_db.get_folder(library_id, folder_id)  # fail fast if the folder is gone

    book_id = uuid.uuid4().hex
    tmp_path = os.path.join(library_db.data_dir(), 'tmp', f'{book_id}.{ext}')
    final_rel = library_db.book_file_rel(library_id, book_id, ext)
    cover_rel = library_db.cover_rel(library_id, book_id)

    try:
        digest = hashlib.sha256()
        size = 0
        with open(tmp_path, 'wb') as out:
            while True:
                chunk = file_storage.stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
        if size == 0:
            raise LibraryError('Il file è vuoto', 400, 'empty')
        sha256 = digest.hexdigest()

        if not allow_duplicate:
            existing = library_db.find_duplicate(library_id, sha256)
            if existing:
                if reuse_duplicate:
                    return existing, True
                raise DuplicateBook(existing)

        try:
            info = extract(tmp_path, ext)
        except BookReadError as e:
            raise LibraryError(str(e), 400, 'unreadable')
        finally:
            _free_pdf_cache()

        final_path = library_db.abs_path(final_rel)
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        os.replace(tmp_path, final_path)

        has_cover = False
        if info.get('cover'):
            cover_path = library_db.abs_path(cover_rel)
            os.makedirs(os.path.dirname(cover_path), exist_ok=True)
            with open(cover_path, 'wb') as fh:
                fh.write(info['cover'])
            has_cover = True

        try:
            book = library_db.create_book(
                library_id, book_id, folder_id, unique=not allow_duplicate,
                title=clean_title(info.get('title'), filename)[:library_db.MAX_TITLE],
                author=info.get('author'),
                original_filename=filename[:255],
                file_type=ext,
                file_size=size,
                file_path=final_rel,
                sha256=sha256,
                language=tagger.detect_language_offline(info.get('text_sample'), info.get('language_code')),
                num_pages=info.get('num_pages'),
                num_chapters=info.get('num_chapters'),
                total_words=info.get('total_words'),
                total_chars=info.get('total_chars'),
                estimated_tokens=info.get('estimated_tokens'),
                has_cover=1 if has_cover else 0,
            )
        except library_db.DuplicateInsert as duplicate:
            # The same file arrived at the same moment through another request
            library_db.remove_files([final_rel, cover_rel])
            existing = library_db.get_book(library_id, duplicate.existing_id)
            if reuse_duplicate and existing:
                return existing, True
            raise DuplicateBook(existing) from None
    except BaseException:
        library_db.remove_files([final_rel, cover_rel])
        raise
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    logger.info(f"Library book added {book_id[:8]} ({ext}, {size} bytes)")
    record = library_db.get_book_record(library_id, book_id)
    schedule_tagging(library_id, book_id, _tagging_context(info), stamp=record['tag_updated_at'] if record else None)
    return book, False


def adopt_existing_file(library_id, path, filename, ext, job_id, folder_id=None):
    """
    Index a book the portal already has (same file, no copy): metadata, cover,
    automatic tags. Used for the books that were in the portal's library before
    the folders existed, and for the ones added later from the old page.
    """
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    try:
        info = extract(path, ext)
    except BookReadError as e:
        logger.warning(f"Metadata not readable for portal book {job_id[:8]}: {e}")
        info = {}
    finally:
        _free_pdf_cache()
    book_id = uuid.uuid4().hex
    cover_rel = library_db.cover_rel(library_id, book_id)
    has_cover = False
    if info.get('cover'):
        cover_path = library_db.abs_path(cover_rel)
        os.makedirs(os.path.dirname(cover_path), exist_ok=True)
        with open(cover_path, 'wb') as fh:
            fh.write(info['cover'])
        has_cover = True
    try:
        book = library_db.create_book(
            library_id, book_id, folder_id, unique=False,
            title=clean_title(info.get('title'), filename)[:library_db.MAX_TITLE],
            author=info.get('author'),
            original_filename=filename[:255],
            file_type=ext,
            file_size=size,
            file_path=library_db.stored_path(path),
            sha256=digest.hexdigest(),
            language=tagger.detect_language_offline(info.get('text_sample'), info.get('language_code')),
            num_pages=info.get('num_pages'),
            num_chapters=info.get('num_chapters'),
            total_words=info.get('total_words'),
            total_chars=info.get('total_chars'),
            estimated_tokens=info.get('estimated_tokens'),
            has_cover=1 if has_cover else 0,
            job_id=job_id,
        )
    except BaseException:
        library_db.remove_files([cover_rel])
        raise
    record = library_db.get_book_record(library_id, book_id)
    schedule_tagging(library_id, book_id, _tagging_context(info) if info else None,
                     stamp=record['tag_updated_at'] if record else None)
    logger.info(f"Library indexed portal book {job_id[:8]} as {book_id[:8]}")
    return book


def _get_executor():
    global _executor
    with _executor_lock:
        if _executor is None:
            workers = max(1, int(os.getenv('LIBRARY_TAGGING_WORKERS', '3')))
            _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='library-tagger')
        return _executor


def schedule_tagging(library_id, book_id, context=None, stamp=None):
    """Queue tagging; `stamp` (tag_updated_at when queued) makes sure a book is tagged once."""
    if TAGGING_INLINE:
        _run_tagging(library_id, book_id, context, stamp)
    else:
        _get_executor().submit(_run_tagging, library_id, book_id, context, stamp)


def _run_tagging(library_id, book_id, context=None, stamp=None):
    try:
        book = library_db.get_book_record(library_id, book_id, include_deleted=True)
        if not book:
            return
        if stamp is not None:
            if not library_db.start_tagging(book_id, stamp):
                return  # re-queued in the meantime (another copy of this job runs it)
        else:
            library_db.set_tag_status(book_id, 'running')
        if context is None:
            info = extract(library_db.abs_path(book['file_path']), book['file_type'], include_cover=False)
            context = _tagging_context(info)
        context = dict(
            context,
            title=book['title'],
            author=book['author'],
            filename=book['original_filename'],
            folder=_folder_label(library_id, book['folder_id']),
        )
        result = tagger.suggest(context, library_db.tag_vocabulary(library_id))
        library_db.apply_tagging(
            library_id, book_id, result['tags'],
            language=result.get('language'), summary=result.get('summary'), method=result['method'],
        )
        logger.info(f"Tagged book {book_id[:8]} via {result['method']}: {', '.join(result['tags']) or '-'}")
    except Exception as e:
        logger.exception(f"Tagging failed for book {book_id[:8]}")
        try:
            library_db.set_tag_status(book_id, 'error', str(e)[:300])
        except Exception:
            pass


def _maintenance(library_id):
    """Cheap periodic housekeeping, at most once a minute per library and process."""
    now = time.time()
    if now - _last_maintenance.get(library_id, 0) < 60:
        return
    if len(_last_maintenance) > 10000:
        _last_maintenance.clear()
    _last_maintenance[library_id] = now
    try:
        library_db.purge_expired(library_id)
        for book_id, stamp in library_db.claim_stale_tagging(library_id):
            schedule_tagging(library_id, book_id, stamp=stamp)
        library_db.fail_stale_translations()
    except Exception:
        logger.exception('Library maintenance failed')
    if ON_MAINTENANCE:
        try:
            ON_MAINTENANCE(library_id)
        except Exception:
            logger.exception('Portal maintenance hook failed')


def storage_warning():
    """On Render the disk is wiped at every deploy unless LIBRARY_DATA_DIR points to a persistent disk."""
    if os.getenv('RENDER') and not os.getenv('LIBRARY_DATA_DIR'):
        return ('I libri sono salvati sul disco temporaneo di Render: a ogni nuovo deploy o riavvio vengono '
                'cancellati. Aggiungi un Persistent Disk al servizio e imposta LIBRARY_DATA_DIR sul suo percorso.')
    return None


if storage_warning():
    logger.warning('LIBRARY_DATA_DIR is not set on Render: the library will be wiped at every deploy')


# ---------------------------------------------------------------------------
# Page & library state
# ---------------------------------------------------------------------------

def library_page():
    if PAGE_ALLOWED is not None and not PAGE_ALLOWED():
        return redirect('/login')
    return render_template('library.html')


@bp.record_once
def _add_page_rule(state):
    # Read when the blueprint is registered, not at import: the portal sets
    # PAGE_PATH first (its own /libreria is another page).
    state.add_url_rule(PAGE_PATH, 'library_page', library_page)


@bp.route('/api/library')
def api_library():
    library_id = current_library_id()
    _maintenance(library_id)
    return jsonify({
        'folders': library_db.list_folders(library_id),
        'books': [serialize_book(b) for b in library_db.list_books(library_id)],
        'tags': library_db.list_tags(library_id),
        'trash_count': library_db.trash_count(library_id),
        'ai': tagger.ai_status(),
        'storage_warning': storage_warning(),
    })


@bp.route('/api/library/summary')
def api_library_summary():
    library_id = current_library_id(create=False)
    return jsonify({'books': library_db.count_books(library_id) if library_id else 0})


@bp.route('/api/library/activity')
def api_library_activity():
    return jsonify(library_db.activity(current_library_id()))


@bp.route('/api/library/key', methods=['GET'])
def api_library_key():
    library = current_library()
    return jsonify({'code': library_db.format_code(library['code'])})


@bp.route('/api/library/key', methods=['POST'])
def api_library_open():
    code = library_db.normalize_code(_json().get('code'))
    if not code:
        raise LibraryError('Codice non valido: controlla di averlo copiato per intero')
    if IDENTITY is not None:
        # Portal: one library per account. The code is the key import_books.py
        # uses; here it is only checked, never used to switch library.
        library = IDENTITY(False)
        if not library or library['code'] != code:
            raise LibraryError('Nessuna libreria trovata con questo codice', 404, 'not_found')
        return jsonify({
            'ok': True,
            'code': library_db.format_code(library['code']),
            'books': library_db.count_books(library['id']),
        })
    library = library_db.get_library_by_code(code)
    if not library:
        raise LibraryError('Nessuna libreria trovata con questo codice', 404, 'not_found')
    g.library = library
    g.library_cookie = library['code']
    return jsonify({
        'ok': True,
        'code': library_db.format_code(library['code']),
        'books': library_db.count_books(library['id']),
    })


@bp.route('/api/library/key/rotate', methods=['POST'])
def api_library_rotate_key():
    library_id = writable_library_id()
    if IDENTITY is not None and not g.get('library_via_login'):
        raise LibraryError('Il codice si cambia dalla pagina, dopo essere entrati', 403, 'login_required')
    code = library_db.rotate_code(library_id)
    if IDENTITY is None:
        g.library_cookie = code
    return jsonify({'code': library_db.format_code(code)})


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

@bp.route('/api/library/folders', methods=['POST'])
def api_create_folder():
    library_id = writable_library_id()
    data = _json()
    folder = library_db.create_folder(
        library_id, data.get('name'),
        parent_id=_id(data.get('parent_id'), 'cartella', optional=True),
        color=data.get('color'),
    )
    return jsonify({'folder': folder}), 201


@bp.route('/api/library/folders/tree', methods=['POST'])
def api_folder_tree():
    library_id = writable_library_id()
    data = _json()
    paths = data.get('paths') or []
    if not isinstance(paths, list) or len(paths) > 2000 or not all(isinstance(p, str) for p in paths):
        raise LibraryError('Elenco cartelle non valido')
    mapping, created = library_db.ensure_folder_paths(
        library_id, _id(data.get('parent_id'), 'cartella', optional=True), paths
    )
    return jsonify({'folders': mapping, 'created': created})


@bp.route('/api/library/folders/<folder_id>', methods=['PATCH'])
def api_update_folder(folder_id):
    library_id = writable_library_id()
    data = _json()
    changes = {key: data[key] for key in ('name', 'color') if key in data}
    folder = library_db.update_folder(library_id, _id(folder_id, 'cartella'), **changes)
    return jsonify({'folder': folder})


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------

@bp.route('/api/library/books', methods=['POST'])
def api_upload_book():
    library_id = writable_library_id()
    file = request.files.get('file')
    if not file or not file.filename:
        raise LibraryError('Nessun file caricato')
    folder_id = _id(request.form.get('folder_id'), 'cartella', optional=True)
    allow_duplicate = request.form.get('allow_duplicate') in ('1', 'true')
    try:
        free_mb = shutil.disk_usage(library_db.data_dir()).free / (1024 * 1024)
    except OSError:
        free_mb = None
    if free_mb is not None and free_mb < MIN_FREE_DISK_MB:
        raise LibraryError('Spazio su disco insufficiente sul server', 507, 'disk_full')
    try:
        book, _ = import_book(library_id, file, folder_id=folder_id, allow_duplicate=allow_duplicate)
    except DuplicateBook as duplicate:
        return jsonify({
            'error': 'Questo libro è già nella tua libreria',
            'code': 'duplicate',
            'existing': serialize_book(duplicate.book),
        }), 409
    if AFTER_IMPORT:
        try:
            AFTER_IMPORT(library_id, book['id'])
        except Exception:
            # the book is safe in the folders; the portal link is retried by the sync
            logger.exception(f"Portal link failed for library book {book['id'][:8]}")
        book = library_db.get_book(library_id, book['id']) or book
    return jsonify({'book': serialize_book(book)}), 201


@bp.route('/api/library/books/update', methods=['POST'])
def api_update_books():
    library_id = writable_library_id()
    data = _json()
    changes = {}
    if 'color' in data:
        changes['color'] = data['color']
    if 'favorite' in data:
        changes['favorite'] = bool(data['favorite'])
    updated = library_db.update_books(library_id, _ids(data.get('book_ids'), 'libro'), **changes)
    return jsonify({'updated': updated})


@bp.route('/api/library/books/tags', methods=['POST'])
def api_book_tags():
    library_id = writable_library_id()
    data = _json()
    book_ids = _ids(data.get('book_ids'), 'libro')
    add = data.get('add') or []
    if not isinstance(add, list) or len(add) > 50 or not all(isinstance(n, str) for n in add):
        raise LibraryError('Tag non validi')
    remove = _ids(data.get('remove'), 'tag')
    added = library_db.add_tags(library_id, book_ids, add) if add else []
    if remove:
        library_db.remove_tags(library_id, book_ids, remove)
    return jsonify({'added': added})


@bp.route('/api/library/books/<book_id>', methods=['GET'])
def api_get_book(book_id):
    library_id = current_library_id(create=False)
    book = library_db.get_book(library_id, _id(book_id, 'libro')) if library_id else None
    if not book:
        raise NotFound('Libro non trovato nella tua libreria')
    return jsonify({'book': serialize_book(book)})


@bp.route('/api/library/books/<book_id>', methods=['PATCH'])
def api_update_book(book_id):
    library_id = writable_library_id()
    data = _json()
    changes = {key: data[key] for key in ('title', 'author', 'color', 'favorite') if key in data}
    if 'favorite' in changes:
        changes['favorite'] = bool(changes['favorite'])
    book = library_db.update_book(library_id, _id(book_id, 'libro'), **changes)
    return jsonify({'book': serialize_book(book)})


@bp.route('/api/library/books/<book_id>/retag', methods=['POST'])
def api_retag_book(book_id):
    library_id = writable_library_id()
    book = library_db.get_book_record(library_id, _id(book_id, 'libro'))
    if not book:
        raise NotFound('Libro non trovato')
    stamp = library_db.set_tag_status(book['id'], 'pending')
    schedule_tagging(library_id, book['id'], stamp=stamp)
    return jsonify({'ok': True})


@bp.route('/api/library/books/<book_id>/cover')
def api_book_cover(book_id):
    library_id = current_library_id(create=False)
    book = library_db.get_book_record(library_id, _id(book_id, 'libro'), include_deleted=True) if library_id else None
    if not book or not book['has_cover']:
        raise NotFound('Copertina non disponibile')
    path = library_db.abs_path(library_db.cover_rel(library_id, book['id']))
    if not os.path.exists(path):
        raise NotFound('Copertina non disponibile')
    response = send_file(path, mimetype='image/jpeg', max_age=31536000)
    response.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    return response


@bp.route('/api/library/books/<book_id>/download')
def api_book_download(book_id):
    library_id = current_library_id(create=False)
    book = library_db.get_book_record(library_id, _id(book_id, 'libro'), include_deleted=True) if library_id else None
    if not book:
        raise NotFound('Libro non trovato')
    path = library_db.abs_path(book['file_path'])
    if not os.path.exists(path):
        raise NotFound('File non trovato')
    return send_file(path, as_attachment=True, download_name=download_name(book['title'], book['file_type']))


# ---------------------------------------------------------------------------
# Organizing
# ---------------------------------------------------------------------------

@bp.route('/api/library/move', methods=['POST'])
def api_move():
    data = _json()
    result = library_db.move_items(
        writable_library_id(),
        book_ids=_ids(data.get('book_ids'), 'libro'),
        folder_ids=_ids(data.get('folder_ids'), 'cartella'),
        target_id=_id(data.get('target_id'), 'cartella', optional=True),
    )
    return jsonify(result)


@bp.route('/api/library/reorder', methods=['POST'])
def api_reorder():
    data = _json()
    kind = data.get('kind')
    count = library_db.reorder(
        writable_library_id(), kind,
        _id(data.get('parent_id'), 'cartella', optional=True),
        _ids(data.get('ids'), 'elemento'),
    )
    return jsonify({'count': count})


@bp.route('/api/library/tags/<tag_id>', methods=['PATCH'])
def api_update_tag(tag_id):
    data = _json()
    changes = {key: data[key] for key in ('name', 'color') if key in data}
    tag = library_db.update_tag(writable_library_id(), _id(tag_id, 'tag'), **changes)
    return jsonify({'tag': tag})


@bp.route('/api/library/tags/<tag_id>', methods=['DELETE'])
def api_delete_tag(tag_id):
    library_db.delete_tag(writable_library_id(), _id(tag_id, 'tag'))
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Trash
# ---------------------------------------------------------------------------

@bp.route('/api/library/trash', methods=['GET'])
def api_trash_list():
    items = library_db.list_trash(current_library_id())
    for item in items:
        if item['kind'] == 'book':
            item['cover_url'] = cover_url(item['id'], item['created_at']) if item['has_cover'] else None
    return jsonify({
        'items': items,
        'retention_days': library_db.TRASH_RETENTION_SECONDS // 86400,
    })


@bp.route('/api/library/trash', methods=['POST'])
def api_trash_add():
    data = _json()
    result = library_db.trash_items(
        writable_library_id(),
        book_ids=_ids(data.get('book_ids'), 'libro'),
        folder_ids=_ids(data.get('folder_ids'), 'cartella'),
    )
    return jsonify(result)


@bp.route('/api/library/trash/restore', methods=['POST'])
def api_trash_restore():
    library_id = writable_library_id()
    data = _json()
    if data.get('trash_id'):
        result = library_db.restore_trash(library_id, _id(data['trash_id'], 'operazione'))
    else:
        result = library_db.restore_items(
            library_id,
            book_ids=_ids(data.get('book_ids'), 'libro'),
            folder_ids=_ids(data.get('folder_ids'), 'cartella'),
        )
    return jsonify(result)


@bp.route('/api/library/trash/purge', methods=['POST'])
def api_trash_purge():
    data = _json()
    result = library_db.purge_items(
        writable_library_id(),
        book_ids=_ids(data.get('book_ids'), 'libro'),
        folder_ids=_ids(data.get('folder_ids'), 'cartella'),
    )
    return jsonify(result)


@bp.route('/api/library/trash/empty', methods=['POST'])
def api_trash_empty():
    return jsonify(library_db.empty_trash(writable_library_id()))


# ---------------------------------------------------------------------------
# Translations of library books
# ---------------------------------------------------------------------------

@bp.route('/api/library/translations/<translation_id>/download')
def api_translation_download(translation_id):
    library_id = current_library_id(create=False)
    record = library_db.get_library_translation(library_id, _translation_id(translation_id)) if library_id else None
    if not record or record['status'] != 'completed':
        raise NotFound('Traduzione non disponibile')
    path = library_db.abs_path(record['output_path'])
    if not path or not os.path.exists(path):
        raise NotFound('File tradotto non trovato')
    filename = record['output_filename'] or f"tradotto.{record['file_type']}"
    return send_file(path, as_attachment=True, download_name=filename)


@bp.route('/api/library/translations/<translation_id>', methods=['DELETE'])
def api_translation_delete(translation_id):
    library_db.delete_translation(writable_library_id(), _translation_id(translation_id))
    return jsonify({'ok': True})
