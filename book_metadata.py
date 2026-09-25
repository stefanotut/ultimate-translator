"""
ULTIMATE TRANSLATOR - Book metadata extraction
Reads everything the library needs from an EPUB or PDF in a single pass:
title, author, statistics (same numbers as the translator's cost estimate),
table of contents, a text sample for automatic tagging and a cover thumbnail.
"""

import os
import re
import html
import logging
import posixpath
import warnings
from urllib.parse import unquote

import fitz  # PyMuPDF
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

from epub_handler import SKIP_TAGS
from translator import estimate_tokens

try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)
except ImportError:  # older bs4
    pass

logger = logging.getLogger(__name__)

COVER_WIDTH = 360
SAMPLE_HEAD_CHARS = 3500
SAMPLE_MIDDLE_CHARS = 1500
MAX_TOC_ENTRIES = 40

_JUNK_TITLES = {
    'untitled', 'senza titolo', 'document', 'documento', 'title', 'titolo', 'book', 'libro',
    'unknown', 'sconosciuto', 'new document', 'nuovo documento', 'copertina', 'cover', 'bozza',
    'draft', 'presentazione', 'presentation', 'ebook', 'pdf', 'layout', 'untitled document',
}
_JUNK_AUTHORS = {
    'user', 'admin', 'administrator', 'amministratore', 'owner', 'proprietario', 'unknown',
    'author', 'autore', 'sconosciuto', 'utente', 'default', 'pc', 'microsoft', 'windows user',
    'microsoft office user', 'utente di windows', 'utente di microsoft office', 'calibre',
}
_FILE_EXT_RE = re.compile(r'\.(docx?|pdf|indd|qxp|qxd|rtf|odt|txt|pages|epub|x?html?|pptx?|key)$', re.I)


class BookReadError(Exception):
    """The file cannot be opened as a book (corrupt, protected, empty)."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract(path, file_type, include_cover=True):
    """
    Returns a dict with: title, author, language_code, description, keywords,
    num_pages, num_chapters, total_chars, total_words, estimated_tokens,
    toc, text_sample, cover (JPEG bytes or None).
    """
    try:
        if file_type == 'epub':
            return _extract_epub(path, include_cover)
        if file_type == 'pdf':
            return _extract_pdf(path, include_cover)
    except BookReadError:
        raise
    except Exception as e:
        logger.warning(f"Could not read {file_type} file: {e}")
        raise BookReadError('Il file sembra danneggiato o non è leggibile') from e
    raise BookReadError('Formato non supportato')


def title_from_filename(filename):
    name = os.path.splitext(os.path.basename(filename or ''))[0]
    name = re.sub(r'[_]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name or 'Libro senza titolo'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_text(value):
    if value is None:
        return ''
    text = html.unescape(str(value))
    return re.sub(r'\s+', ' ', text).strip()


def _good_title(value):
    title = _clean_text(value)
    if len(title) < 2:
        return None
    low = title.lower()
    if low in _JUNK_TITLES or re.sub(r'[\s_\-]*\d+$', '', low) in _JUNK_TITLES:
        return None
    if low.startswith(('microsoft word', 'microsoft powerpoint', 'untitled', 'senza titolo')):
        return None
    if _FILE_EXT_RE.search(low):
        return None
    if sum(c.isalpha() for c in title) < 2:
        return None
    return title[:300]


def _good_author(value):
    author = _clean_text(value)
    if len(author) < 2 or author.lower() in _JUNK_AUTHORS:
        return None
    if '@' in author or sum(c.isalpha() for c in author) < 2:
        return None
    return author[:200]


def _estimate(all_text):
    """Same estimation as analyze_epub / analyze_pdf, so costs match the translator."""
    estimated = estimate_tokens(all_text[:50000])
    if len(all_text) > 50000:
        estimated = int(estimated * (len(all_text) / 50000))
    return estimated


def _build_sample(texts):
    parts = [re.sub(r'\s+', ' ', t).strip() for t in texts]
    substantial = [p for p in parts if len(p) >= 80]
    parts = substantial or [p for p in parts if p]
    if not parts:
        return ''
    joined = '\n'.join(parts)
    if len(joined) > SAMPLE_HEAD_CHARS + SAMPLE_MIDDLE_CHARS * 2:
        middle = len(joined) // 2
        return joined[:SAMPLE_HEAD_CHARS] + '\n[...]\n' + joined[middle:middle + SAMPLE_MIDDLE_CHARS]
    return joined[:SAMPLE_HEAD_CHARS + SAMPLE_MIDDLE_CHARS]


def _sniff_image(data):
    if not data:
        return None
    if data[:3] == b'\xff\xd8\xff':
        return 'jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    if data[:2] == b'BM':
        return 'bmp'
    head = data[:2048].lstrip().lower()
    if head.startswith(b'<svg') or (head.startswith(b'<?xml') and b'<svg' in head):
        return 'svg'
    return None


def _render_thumbnail(page, min_side=0):
    rect = page.rect
    if rect.width < 1 or rect.height < 1 or min(rect.width, rect.height) < min_side:
        return None
    zoom = min(COVER_WIDTH / rect.width, 4.0)
    max_height = COVER_WIDTH * 2.2
    if rect.height * zoom > max_height:
        zoom = max_height / rect.height
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes('jpg', jpg_quality=82)


def _thumbnail_from_image(data):
    kind = _sniff_image(data)
    if not kind:
        return None
    try:
        with fitz.open(stream=data, filetype=kind) as doc:
            if doc.page_count < 1:
                return None
            return _render_thumbnail(doc[0], min_side=100)
    except Exception as e:
        logger.debug(f"Cover image not usable: {e}")
        return None


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

def _meta_values(book, namespace, name):
    try:
        return [value for value, _attrs in book.get_metadata(namespace, name) if value]
    except Exception:
        return []


def _resolve_href(base_name, href):
    href = unquote((href or '').split('#')[0].strip())
    if not href or re.match(r'^[a-z]+:', href, re.I):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_name or ''), href))


def _epub_cover(book, documents):
    candidates = list(book.get_items_of_type(ebooklib.ITEM_COVER))

    try:
        for _value, attrs in book.get_metadata('OPF', 'cover'):
            item = book.get_item_with_id((attrs or {}).get('content'))
            if item:
                candidates.append(item)
    except Exception:
        pass

    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        if 'cover' in (item.get_name() or '').lower() or 'cover' in (item.get_id() or '').lower():
            candidates.append(item)

    # Cover pages usually just wrap an <img> or an SVG <image>
    for doc in documents[:3]:
        try:
            soup = BeautifulSoup(doc.get_content(), 'html.parser')
        except Exception:
            continue
        for tag in soup.find_all(['img', 'image']):
            src = tag.get('src') or tag.get('xlink:href') or tag.get('href')
            resolved = _resolve_href(doc.get_name(), src)
            item = book.get_item_with_href(resolved) if resolved else None
            if item:
                candidates.append(item)

    seen = set()
    for item in candidates:
        if id(item) in seen:
            continue
        seen.add(id(item))
        try:
            thumb = _thumbnail_from_image(item.get_content())
        except Exception:
            thumb = None
        if thumb:
            return thumb
    return None


def _extract_epub(path, include_cover):
    book = epub.read_epub(path, options={'ignore_ncx': True})
    documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

    total_chars = 0
    total_words = 0
    chapters = 0
    headings = []
    texts = []
    all_text = ''

    for doc_item in documents:
        try:
            content = doc_item.get_content().decode('utf-8', errors='replace')
        except Exception:
            continue
        soup = BeautifulSoup(content, 'html.parser')
        heading = soup.find(['h1', 'h2', 'h3'])
        heading_text = heading.get_text(strip=True) if heading else ''
        for tag in soup.find_all(SKIP_TAGS):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        if text and len(text.strip()) > 10:
            total_chars += len(text)
            total_words += len(text.split())
            all_text += text + ' '
            texts.append(text)
            chapters += 1
            if heading_text:
                headings.append(heading_text[:120])

    description = ' '.join(
        BeautifulSoup(d, 'html.parser').get_text(' ', strip=True)
        for d in _meta_values(book, 'DC', 'description')
    )
    creators = [a for a in (_good_author(c) for c in _meta_values(book, 'DC', 'creator')) if a]
    languages = _meta_values(book, 'DC', 'language')

    return {
        'title': next((t for t in (_good_title(v) for v in _meta_values(book, 'DC', 'title')) if t), None),
        'author': ', '.join(dict.fromkeys(creators[:3])) or None,
        'language_code': languages[0].strip().lower()[:12] if languages else None,
        'description': _clean_text(description)[:1500] or None,
        'keywords': [_clean_text(s)[:60] for s in _meta_values(book, 'DC', 'subject')][:15],
        'num_pages': None,
        'num_chapters': chapters,
        'total_chars': total_chars,
        'total_words': total_words,
        'estimated_tokens': _estimate(all_text),
        'toc': list(dict.fromkeys(headings))[:MAX_TOC_ENTRIES],
        'text_sample': _build_sample(texts),
        'cover': _epub_cover(book, documents) if include_cover else None,
    }


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _extract_pdf(path, include_cover):
    doc = fitz.open(path)
    try:
        if doc.needs_pass and not doc.authenticate(''):
            raise BookReadError('Il PDF è protetto da password')
        if doc.page_count == 0:
            raise BookReadError('Il PDF non contiene pagine')

        total_chars = 0
        total_words = 0
        texts = []
        all_text = ''
        for page in doc:
            text = page.get_text('text')
            texts.append(text)
            if text:
                total_chars += len(text)
                total_words += len(text.split())
                all_text += text + ' '

        meta = doc.metadata or {}
        try:
            toc = [_clean_text(entry[1])[:120] for entry in doc.get_toc(simple=True) if entry[0] <= 2]
        except Exception:
            toc = []
        keywords = [k.strip()[:60] for k in re.split(r'[,;]', meta.get('keywords') or '') if k.strip()]

        cover = None
        if include_cover:
            try:
                cover = _render_thumbnail(doc[0])
            except Exception as e:
                logger.debug(f"PDF cover render failed: {e}")

        return {
            'title': _good_title(meta.get('title')),
            'author': _good_author(meta.get('author')),
            'language_code': None,
            'description': _clean_text(meta.get('subject'))[:1500] or None,
            'keywords': keywords[:15],
            'num_pages': doc.page_count,
            'num_chapters': None,
            'total_chars': total_chars,
            'total_words': total_words,
            'estimated_tokens': _estimate(all_text),
            'toc': [t for t in dict.fromkeys(toc) if t][:MAX_TOC_ENTRIES],
            'text_sample': _build_sample(texts),
            'cover': cover,
        }
    finally:
        doc.close()
