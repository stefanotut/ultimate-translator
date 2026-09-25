"""
ULTIMATE TRANSLATOR - EPUB Handler
Translates EPUB files preserving all formatting, images, styles, and structure.
Uses context-aware, batched translation for coherent book-length output.
Supports both OpenAI and Anthropic providers.

The translated book is the original archive with only the text of its
chapters and table of contents replaced: stylesheets, fonts, images, metadata
and markup stay exactly as they were.
"""

import os
import re
import html.entities
import logging
import posixpath
import zipfile
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, Tag, UnicodeDammit
from bs4.element import CData, Comment, Declaration, Doctype, ProcessingInstruction
from lxml import etree

from translator import (
    DEFAULT_MODELS, FatalTranslationError, TranslationError, TranslationSession,
    estimate_tokens, needs_translation,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag classification
# ---------------------------------------------------------------------------

SKIP_TAGS = frozenset({
    'script', 'style', 'code', 'pre', 'svg', 'math', 'img', 'video',
    'audio', 'source', 'link', 'meta', 'noscript', 'object', 'embed',
    'iframe', 'canvas', 'map', 'area',
})

INLINE_TAGS = frozenset({
    'a', 'abbr', 'b', 'bdi', 'bdo', 'br', 'cite', 'data', 'dfn', 'em',
    'i', 'kbd', 'mark', 'q', 'rb', 'rp', 'rt', 'rtc', 'ruby', 's',
    'samp', 'small', 'span', 'strong', 'sub', 'sup', 'time', 'u', 'var',
    'wbr', 'del', 'ins',
})

BLOCK_TAGS = frozenset({
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'dt',
    'dd', 'blockquote', 'figcaption', 'caption', 'summary', 'label',
    'legend', 'title', 'div', 'section', 'article', 'aside', 'header',
    'footer', 'main', 'details',
})

# Wraps text that sits next to block elements (<li>Item<ul>...</ul></li>) while it is translated
RUN_TAG = 'ut-run'

DOCUMENT_TYPES = ('application/xhtml+xml', 'text/html')
NCX_TYPE = 'application/x-dtbncx+xml'

LANGUAGE_CODES = {
    'English': 'en', 'Italian': 'it', 'French': 'fr', 'German': 'de', 'Spanish': 'es',
    'Portuguese': 'pt', 'Dutch': 'nl', 'Russian': 'ru', 'Chinese': 'zh', 'Japanese': 'ja',
    'Korean': 'ko', 'Arabic': 'ar', 'Polish': 'pl', 'Swedish': 'sv', 'Norwegian': 'no',
    'Danish': 'da', 'Finnish': 'fi', 'Czech': 'cs', 'Turkish': 'tr', 'Hindi': 'hi',
    'Greek': 'el', 'Romanian': 'ro', 'Hungarian': 'hu', 'Thai': 'th', 'Vietnamese': 'vi',
    'Hebrew': 'he', 'Ukrainian': 'uk', 'Persian': 'fa', 'Indonesian': 'id', 'Catalan': 'ca',
}
RTL_LANGUAGES = frozenset({'Arabic', 'Hebrew', 'Persian', 'Urdu'})

# SVG names that are camelCase (the lenient HTML parser lowercases them)
_SVG_CASE = {name.lower(): name for name in (
    'viewBox', 'preserveAspectRatio', 'linearGradient', 'radialGradient', 'gradientUnits',
    'gradientTransform', 'clipPath', 'clipPathUnits', 'patternUnits', 'patternTransform',
    'patternContentUnits', 'textPath', 'foreignObject', 'markerWidth', 'markerHeight',
    'markerUnits', 'refX', 'refY', 'stdDeviation', 'maskUnits', 'maskContentUnits',
    'feGaussianBlur', 'feOffset', 'feBlend', 'feColorMatrix', 'feMerge', 'feMergeNode',
    'baseProfile', 'spreadMethod', 'startOffset', 'textLength', 'lengthAdjust',
)}
_XML_ENTITIES = frozenset({'amp', 'lt', 'gt', 'quot', 'apos'})


def language_code(language):
    """ISO 639-1 code for a language name of the translator ('Italian' -> 'it')."""
    if language in LANGUAGE_CODES:
        return LANGUAGE_CODES[language]
    return (language or 'und')[:2].lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_translatable(text: str) -> bool:
    """Check if text contains translatable alphabetic content."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return any(c.isalpha() for c in stripped)


def _has_only_inline_children(tag: Tag) -> bool:
    """Return True if a tag contains only inline elements and text nodes."""
    for child in tag.children:
        if isinstance(child, Tag):
            if child.name not in INLINE_TAGS and child.name not in SKIP_TAGS:
                return False
    return True


def _is_inside_skip_tag(tag: Tag) -> bool:
    """Check if a tag is nested inside a SKIP_TAGS ancestor."""
    for parent in tag.parents:
        if isinstance(parent, Tag) and parent.name in SKIP_TAGS:
            return True
    return False


def _extract_text_sample(html_content: str, max_chars: int = 1000) -> str:
    """Extract a plain text sample from HTML for language detection."""
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator=' ', strip=True)
    return text[:max_chars]


def _numeric_entities(text):
    """HTML named entities (&nbsp;) as numeric ones, which an XML parser understands."""
    def replace(match):
        name = match.group(1)
        if name in _XML_ENTITIES or name not in html.entities.name2codepoint:
            return match.group(0)
        return f'&#{html.entities.name2codepoint[name]};'
    return re.sub(r'&([A-Za-z][A-Za-z0-9]*);', replace, text)


def _decode(raw):
    """Text of an archive entry, whatever its declared encoding."""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return UnicodeDammit(raw, is_html=True).unicode_markup or raw.decode('utf-8', errors='replace')


def _is_well_formed(text):
    try:
        etree.fromstring(text.encode('utf-8'))
        return True
    except (etree.XMLSyntaxError, ValueError):
        return False


def _parse_document(content):
    """(soup, xml_mode): XHTML is parsed as XML, which keeps it exactly; broken markup as HTML."""
    fixed = _numeric_entities(content)
    if _is_well_formed(fixed):
        return BeautifulSoup(fixed, 'lxml-xml'), True
    return BeautifulSoup(content, 'html.parser'), False


def _fragment_nodes(fragment, xml_mode):
    """Nodes of a translated HTML fragment, ready to be put back in the document."""
    if xml_mode:
        wrapped = ('<ut-root xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                   + _numeric_entities(fragment) + '</ut-root>')
        if _is_well_formed(wrapped):
            return list(BeautifulSoup(wrapped, 'lxml-xml').find('ut-root').children)
    return list(BeautifulSoup(fragment, 'html.parser').children)


def _fix_svg_case(markup):
    """The HTML parser lowercases names; SVG needs its camelCase ones back (viewBox, linearGradient...)."""
    def fix_names(tag):
        return re.sub(r'(?<=[<\s/])([A-Za-z][A-Za-z0-9-]*)',
                      lambda m: _SVG_CASE.get(m.group(1), m.group(1)), tag.group(0))

    def fix_svg(svg):
        return re.sub(r'<[^<>]+>', fix_names, svg.group(0))

    return re.sub(r'<svg\b.*?</svg>', fix_svg, markup, flags=re.S | re.I)


def _serialize(soup, xml_mode):
    markup = str(soup) if xml_mode else _fix_svg_case(str(soup))
    # The text is written back as UTF-8: the declarations must say so
    markup = re.sub(r'(<\?xml[^>]*encoding=["\'])[^"\']+(["\'])', r'\1utf-8\2', markup, count=1)
    markup = re.sub(r'(<meta[^>]*charset=["\']?)[A-Za-z0-9_-]+', r'\1utf-8', markup, flags=re.I)
    return markup


def _is_text_node(node):
    return isinstance(node, NavigableString) and not isinstance(
        node, (Comment, CData, ProcessingInstruction, Declaration, Doctype))


def _wrap_loose_runs(soup):
    """Wrap inline text that sits next to block elements (<li>Item<ul>..</ul></li>), so that it is translated too."""
    for tag in list(soup.find_all(True)):
        if tag.name in SKIP_TAGS or tag.name in INLINE_TAGS or tag.name in ('html', 'head', RUN_TAG):
            continue
        children = list(tag.children)
        if _has_only_inline_children(tag) or _is_inside_skip_tag(tag):
            continue  # only inline content: the element is translated as one block
        run = []
        for child in children + [None]:
            if child is not None and (_is_text_node(child) or (
                    isinstance(child, Tag) and (child.name in INLINE_TAGS or child.name == 'img'))):
                run.append(child)
                continue
            if run and needs_translation(''.join(n.get_text() if isinstance(n, Tag) else str(n) for n in run)):
                wrapper = soup.new_tag(RUN_TAG)
                run[0].insert_before(wrapper)
                for node in run:
                    wrapper.append(node.extract())
            run = []


def _translatable_blocks(soup):
    """Innermost block elements with inline-only content that has text to translate."""
    blocks = []
    for tag in soup.find_all(True):
        if tag.name in SKIP_TAGS or _is_inside_skip_tag(tag):
            continue
        if (tag.name in BLOCK_TAGS or tag.name == RUN_TAG) and _has_only_inline_children(tag):
            if needs_translation(tag.get_text()):
                blocks.append(tag)

    # Deduplicate: remove blocks that are ancestors of other blocks
    block_ids = set(id(b) for b in blocks)
    return [
        _innermost_wrapper(block) for block in blocks
        if not any(isinstance(d, Tag) and id(d) in block_ids for d in block.descendants)
    ]


def _innermost_wrapper(tag):
    """
    <li><a href="..">Title</a></li> -> the <a>: when one inline element holds all the text,
    only its content is sent to the model, so the element and its attributes cannot be lost
    (and navigation documents keep the structure EPUB requires).
    """
    while True:
        elements = [c for c in tag.children if isinstance(c, Tag)]
        loose_text = any(_is_text_node(c) and c.strip() for c in tag.children)
        if len(elements) != 1 or loose_text:
            return tag
        child = elements[0]
        if child.name not in INLINE_TAGS or child.name in ('br', 'wbr') or not needs_translation(child.get_text()):
            return tag
        tag = child


def _set_language(soup, target_lang):
    root = soup.find('html')
    if not isinstance(root, Tag):
        return
    code = language_code(target_lang)
    root['lang'] = code
    root['xml:lang'] = code
    if target_lang in RTL_LANGUAGES:
        root['dir'] = 'rtl'
    elif root.get('dir') == 'rtl':
        del root['dir']


def _plain(markup):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', markup or '')).strip()


# ---------------------------------------------------------------------------
# EPUB package (container.xml -> OPF -> manifest, spine)
# ---------------------------------------------------------------------------

MAX_UNCOMPRESSED = 300 * 1024 * 1024   # zip bombs are refused before anything is read
MAX_ENTRY = 64 * 1024 * 1024


class EpubPackage:
    """Reading order and special files of an EPUB archive (without rewriting anything)."""

    def __init__(self, archive):
        self.archive = archive
        infos = archive.infolist()
        if any(i.file_size > MAX_ENTRY for i in infos) or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
            raise TranslationError("EPUB non valido: e troppo grande una volta decompresso")
        names = archive.namelist()
        self.names = set(names)
        lookup = {name.lower(): name for name in names}
        try:
            container = archive.read('META-INF/container.xml')
        except KeyError:
            raise TranslationError('EPUB non valido: manca META-INF/container.xml') from None
        match = re.search(rb'full-path\s*=\s*["\']([^"\']+)["\']', container)
        if not match:
            raise TranslationError('EPUB non valido: container.xml non indica il file OPF')
        opf_path = unquote(match.group(1).decode('utf-8', errors='replace'))
        self.opf_path = opf_path if opf_path in self.names else lookup.get(opf_path.lower())
        if not self.opf_path:
            raise TranslationError(f'EPUB non valido: manca il file {opf_path}')
        opf_dir = posixpath.dirname(self.opf_path)
        opf = BeautifulSoup(_numeric_entities(_decode(archive.read(self.opf_path))), 'lxml-xml')

        items = {}
        for item in opf.find_all('item'):
            href = item.get('href')
            if not href:
                continue
            path = posixpath.normpath(posixpath.join(opf_dir, unquote(href.split('#')[0])))
            path = path if path in self.names else lookup.get(path.lower())
            items[item.get('id')] = (path, (item.get('media-type') or '').lower())
        spine = [items[ref.get('idref')] for ref in opf.find_all('itemref') if ref.get('idref') in items]

        self.documents = []
        seen = set()
        for path, media_type in spine + list(items.values()):
            if path and path not in seen and media_type in DOCUMENT_TYPES:
                self.documents.append(path)
                seen.add(path)
        self.ncx = next((path for path, media_type in items.values() if path and media_type == NCX_TYPE), None)
        self.drm = self._has_drm(set(self.documents))

    def _has_drm(self, documents):
        """True when chapters are encrypted (font obfuscation alone is harmless)."""
        if 'META-INF/rights.xml' in self.names:
            return True
        if 'META-INF/encryption.xml' not in self.names:
            return False
        encryption = self.archive.read('META-INF/encryption.xml').decode('utf-8', errors='replace')
        for uri in re.findall(r'<(?:\w+:)?CipherReference[^>]*URI\s*=\s*["\']([^"\']+)["\']', encryption):
            if posixpath.normpath(unquote(uri)) in documents:
                return True
        return False

    def read_text(self, path):
        return _decode(self.archive.read(path))


# ---------------------------------------------------------------------------
# EPUB analysis (for cost estimation)
# ---------------------------------------------------------------------------

def analyze_epub(input_path: str) -> dict:
    """
    Analyze an EPUB file and return statistics for cost estimation.

    Returns:
        dict with: total_chars, total_words, num_chapters, chapter_names, estimated_tokens
    """
    total_chars = 0
    total_words = 0
    chapter_names = []
    all_text = ""

    with zipfile.ZipFile(input_path) as archive:
        package = EpubPackage(archive)
        for path in package.documents:
            try:
                content = package.read_text(path)
            except Exception:
                continue

            text = _extract_text_sample(content, max_chars=999999)
            if text and len(text.strip()) > 10:
                total_chars += len(text)
                total_words += len(text.split())
                all_text += text + " "

                # Try to extract a readable chapter name from the content
                soup = BeautifulSoup(content, 'html.parser')
                heading = soup.find(['h1', 'h2', 'h3'])
                if heading and heading.get_text(strip=True):
                    chapter_names.append(heading.get_text(strip=True)[:80])
                else:
                    chapter_names.append(posixpath.basename(path))

    estimated_tokens = estimate_tokens(all_text[:50000])
    # Scale up if text was truncated
    if len(all_text) > 50000:
        ratio = len(all_text) / 50000
        estimated_tokens = int(estimated_tokens * ratio)

    return {
        "total_chars": total_chars,
        "total_words": total_words,
        "num_chapters": len(chapter_names),
        "chapter_names": chapter_names[:30],  # Limit for API response
        "estimated_tokens": estimated_tokens,
    }


# ---------------------------------------------------------------------------
# Chapter translation
# ---------------------------------------------------------------------------

def _translate_document(content, session, target_lang, previous_context="", on_progress=None):
    """(translated XHTML, plain-text tail for the next chapter's context)."""
    soup, xml_mode = _parse_document(content)
    _wrap_loose_runs(soup)
    blocks = _translatable_blocks(soup)
    sources = [block.decode_contents() for block in blocks]
    translated = session.translate_batch(sources, previous_context, on_progress=on_progress)

    for block, source, value in zip(blocks, sources, translated):
        if value == source:
            continue
        block.clear()
        for node in _fragment_nodes(value, xml_mode):
            block.append(node.extract())
    for wrapper in soup.find_all(RUN_TAG):
        wrapper.unwrap()
    _set_language(soup, target_lang)

    tail = _plain(' '.join(translated[-6:]))[-800:]
    return _serialize(soup, xml_mode), tail or previous_context


def translate_html_content(
    html_content: str,
    source_lang: str,
    target_lang: str,
    provider: str = "anthropic",
    model: str = DEFAULT_MODELS["anthropic"],
    progress_callback=None,
    session=None,
) -> str:
    """
    Translate HTML content of an EPUB chapter.
    """
    session = session or TranslationSession(source_lang, target_lang, model)
    translated, _ = _translate_document(html_content, session, target_lang, on_progress=progress_callback)
    return translated


def _translate_ncx(content, session):
    """Table of contents of EPUB 2 readers: translate the chapter titles."""
    soup = BeautifulSoup(_numeric_entities(content), 'lxml-xml')
    labels = [text for text in soup.find_all('text') if text.find_parent(['navLabel', 'docTitle'])]
    sources = [label.get_text() for label in labels]
    translated = session.translate_batch(sources)
    for label, source, value in zip(labels, sources, translated):
        if value != source:
            label.string = _plain(value)
    return str(soup)


def _set_opf_language(content, target_lang):
    code = language_code(target_lang)
    return re.sub(r'(<dc:language\b[^>]*>)[^<]*(</dc:language>)', lambda m: m.group(1) + code + m.group(2),
                  content, count=1)


def _write_archive(archive, output_path, replaced):
    """The original EPUB with some entries replaced (mimetype first and uncompressed, as required)."""
    partial = output_path + '.part'
    try:
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as out:
            mimetype = b'application/epub+zip'
            if 'mimetype' in archive.namelist():
                mimetype = archive.read('mimetype').strip() or mimetype
            out.writestr(zipfile.ZipInfo('mimetype'), mimetype, compress_type=zipfile.ZIP_STORED)
            written = {'mimetype'}
            for info in archive.infolist():
                if info.filename in written:
                    continue
                written.add(info.filename)
                data = replaced.get(info.filename)
                if data is None:
                    data = archive.read(info.filename)
                entry = zipfile.ZipInfo(info.filename, date_time=max(info.date_time, (1980, 1, 1, 0, 0, 0)))
                entry.external_attr = info.external_attr
                entry.compress_type = zipfile.ZIP_DEFLATED
                out.writestr(entry, data)
        os.replace(partial, output_path)
    except Exception:
        if os.path.exists(partial):
            os.remove(partial)
        raise


# ---------------------------------------------------------------------------
# Full EPUB translation
# ---------------------------------------------------------------------------

def translate_epub(
    input_path: str,
    output_path: str,
    source_lang: str = "English",
    target_lang: str = "Italian",
    provider: str = "anthropic",
    model: str = DEFAULT_MODELS["anthropic"],
    progress_callback=None,
    session=None,
):
    """
    Translate an entire EPUB file.
    Preserves: images, CSS, fonts, metadata, cover, TOC and markup (byte for byte).
    Translates: all text content of the HTML documents and the NCX table of contents.
    Raises TranslationError when nothing could be translated.
    """
    session = session or TranslationSession(source_lang, target_lang, model)
    try:
        archive = zipfile.ZipFile(input_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise TranslationError(f"EPUB non leggibile (file danneggiato o non e un EPUB): {e}") from e

    with archive:
        package = EpubPackage(archive)
        if package.drm:
            raise TranslationError("Questo EPUB e protetto da DRM: il testo e cifrato e non puo essere tradotto.")

        documents = package.documents
        total_docs = len(documents)
        logger.info(f"EPUB has {total_docs} HTML documents to translate")

        replaced = {}
        failed = []
        context = ""
        for doc_idx, path in enumerate(documents):
            try:
                content = package.read_text(path)
            except Exception as e:
                logger.warning(f"  Could not read document {path}: {e}")
                failed.append(e)
                continue

            if not _is_translatable(_extract_text_sample(content)):
                logger.info(f"  Skipping document {doc_idx + 1}/{total_docs} (no translatable text)")
                if progress_callback:
                    progress_callback(
                        (doc_idx + 1) / total_docs,
                        f"Capitolo {doc_idx + 1}/{total_docs} - Saltato (nessun testo)"
                    )
                continue

            logger.info(f"  Translating document {doc_idx + 1}/{total_docs}: {path}")

            def chapter_progress(current, total, _doc_idx=doc_idx):
                if progress_callback:
                    overall = (_doc_idx / total_docs) + (current / max(total, 1) / total_docs)
                    progress_callback(
                        min(overall, 0.99),
                        f"Capitolo {_doc_idx + 1}/{total_docs} - Blocco {current}/{total}"
                    )

            try:
                translated, context = _translate_document(
                    content, session, target_lang, context, on_progress=chapter_progress
                )
            except FatalTranslationError:
                raise
            except Exception as e:
                logger.exception(f"  Error translating document {doc_idx + 1}")
                failed.append(e)
                if progress_callback:
                    progress_callback(
                        (doc_idx + 1) / total_docs,
                        f"Capitolo {doc_idx + 1}/{total_docs} - ERRORE: {str(e)[:60]}"
                    )
                continue
            replaced[path] = translated.encode('utf-8')

        if session.passages == 0:
            if failed:
                raise TranslationError(f"Impossibile leggere i capitoli di questo EPUB: {failed[0]}")
            raise TranslationError("Questo EPUB non contiene testo da tradurre.")
        if session.passages == session.untranslated:
            raise TranslationError(
                "Nessuna parte del libro e stata tradotta: il modello ha rifiutato tutte le sezioni. "
                "Prova con un altro modello."
            )

        if package.ncx:
            try:
                replaced[package.ncx] = _translate_ncx(package.read_text(package.ncx), session).encode('utf-8')
            except FatalTranslationError:
                raise
            except Exception as e:
                logger.warning(f"Could not translate the NCX table of contents: {e}")

        if progress_callback:
            progress_callback(0.99, "Salvataggio EPUB...")

        opf = replaced.get(package.opf_path)
        opf_text = opf.decode('utf-8') if opf else package.read_text(package.opf_path)
        replaced[package.opf_path] = _set_opf_language(opf_text, target_lang).encode('utf-8')

        _write_archive(archive, output_path, replaced)

    if failed:
        logger.warning(f"{len(failed)} EPUB documents could not be translated")
    logger.info(f"Translated EPUB saved to: {output_path}")

    if progress_callback:
        progress_callback(1.0, "Completato!")
    return {"failed_documents": len(failed)}
