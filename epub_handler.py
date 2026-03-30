"""
ULTIMATE TRANSLATOR - EPUB Handler
Translates EPUB files preserving all formatting, images, styles, and structure.
Uses context-aware translation for coherent book-length output.
Supports both OpenAI and Anthropic providers.
"""

import os
import copy
import logging
import re
from bs4 import BeautifulSoup, NavigableString, Comment, Tag
import ebooklib
from ebooklib import epub
from translator import translate_text, estimate_tokens

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


# ---------------------------------------------------------------------------
# EPUB analysis (for cost estimation)
# ---------------------------------------------------------------------------

def analyze_epub(input_path: str) -> dict:
    """
    Analyze an EPUB file and return statistics for cost estimation.

    Returns:
        dict with: total_chars, total_words, num_chapters, chapter_names, estimated_tokens
    """
    book = epub.read_epub(input_path, options={'ignore_ncx': True})
    documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

    total_chars = 0
    total_words = 0
    chapter_names = []
    all_text = ""

    for doc_item in documents:
        try:
            content = doc_item.get_content().decode('utf-8', errors='replace')
        except Exception:
            continue

        text = _extract_text_sample(content, max_chars=999999)
        if text and len(text.strip()) > 10:
            total_chars += len(text)
            total_words += len(text.split())
            all_text += text + " "

            name = doc_item.get_name()
            # Try to extract a readable chapter name from the content
            soup = BeautifulSoup(content, 'html.parser')
            heading = soup.find(['h1', 'h2', 'h3'])
            if heading and heading.get_text(strip=True):
                chapter_names.append(heading.get_text(strip=True)[:80])
            else:
                chapter_names.append(name)

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
# Block-level HTML translation
# ---------------------------------------------------------------------------

def _translate_block_html(
    html_str: str,
    source_lang: str,
    target_lang: str,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
    previous_context: str = "",
) -> str:
    """
    Translate the inner HTML of a block element as a single unit,
    preserving inline tags. Passes previous context for coherence.
    """
    if not _is_translatable(html_str):
        return html_str

    translated = translate_text(
        html_str,
        source_lang,
        target_lang,
        provider=provider,
        model=model,
        previous_context=previous_context,
    )
    return translated


# ---------------------------------------------------------------------------
# Chapter translation
# ---------------------------------------------------------------------------

def translate_html_content(
    html_content: str,
    source_lang: str,
    target_lang: str,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
    progress_callback=None,
) -> str:
    """
    Translate HTML content of an EPUB chapter.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    blocks = []
    for tag in soup.find_all(True):
        if tag.name in SKIP_TAGS:
            continue
        if _is_inside_skip_tag(tag):
            continue
        if tag.name in BLOCK_TAGS:
            if _has_only_inline_children(tag):
                inner = tag.decode_contents()
                if _is_translatable(inner):
                    blocks.append(tag)

    # Deduplicate: remove blocks that are ancestors of other blocks
    block_set = set(id(b) for b in blocks)
    filtered_blocks = []
    for block in blocks:
        has_child_block = False
        for desc in block.descendants:
            if isinstance(desc, Tag) and id(desc) in block_set and id(desc) != id(block):
                has_child_block = True
                break
        if not has_child_block:
            filtered_blocks.append(block)

    total = len(filtered_blocks)
    translated_count = 0
    previous_translated = ""

    for block in filtered_blocks:
        inner_html = block.decode_contents()
        if not _is_translatable(inner_html):
            continue

        translated_html = _translate_block_html(
            inner_html,
            source_lang,
            target_lang,
            provider=provider,
            model=model,
            previous_context=previous_translated,
        )

        plain_translated = BeautifulSoup(translated_html, 'html.parser').get_text()
        previous_translated = plain_translated

        new_contents = BeautifulSoup(translated_html, 'html.parser')
        block.clear()
        for child in list(new_contents.children):
            block.append(copy.copy(child))

        translated_count += 1
        if progress_callback and total > 0:
            progress_callback(translated_count, total)

    return str(soup)


# ---------------------------------------------------------------------------
# Full EPUB translation
# ---------------------------------------------------------------------------

def translate_epub(
    input_path: str,
    output_path: str,
    source_lang: str = "English",
    target_lang: str = "Italian",
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
    progress_callback=None,
):
    """
    Translate an entire EPUB file.
    Preserves: images, CSS, fonts, metadata structure, cover, TOC, NCX.
    Translates: all text content in HTML documents.
    """
    book = epub.read_epub(input_path, options={'ignore_ncx': False})

    documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    total_docs = len(documents)

    logger.info(f"EPUB has {total_docs} HTML documents to translate")

    if total_docs == 0:
        logger.warning("No HTML documents found in EPUB")
        epub.write_epub(output_path, book)
        if progress_callback:
            progress_callback(1.0, "Completato! (nessun contenuto testuale trovato)")
        return

    for doc_idx, item in enumerate(documents):
        try:
            content = item.get_content().decode('utf-8', errors='replace')
        except Exception as e:
            logger.warning(f"  Could not decode document {doc_idx + 1}: {e}")
            continue

        if not _is_translatable(_extract_text_sample(content)):
            logger.info(f"  Skipping document {doc_idx + 1}/{total_docs} (no translatable text)")
            if progress_callback:
                progress_callback(
                    (doc_idx + 1) / total_docs,
                    f"Capitolo {doc_idx + 1}/{total_docs} - Saltato (nessun testo)"
                )
            continue

        logger.info(f"  Translating document {doc_idx + 1}/{total_docs}: {item.get_name()}")

        def chapter_progress(current, total, _doc_idx=doc_idx):
            if progress_callback:
                overall = (_doc_idx / total_docs) + (current / max(total, 1) / total_docs)
                progress_callback(
                    min(overall, 0.99),
                    f"Capitolo {_doc_idx + 1}/{total_docs} - Blocco {current}/{total}"
                )

        try:
            translated_content = translate_html_content(
                content, source_lang, target_lang,
                provider=provider, model=model,
                progress_callback=chapter_progress
            )
            item.set_content(translated_content.encode('utf-8'))
        except Exception as e:
            logger.error(f"  Error translating document {doc_idx + 1}: {e}")
            if progress_callback:
                progress_callback(
                    (doc_idx + 1) / total_docs,
                    f"Capitolo {doc_idx + 1}/{total_docs} - ERRORE: {str(e)[:60]}"
                )

    if progress_callback:
        progress_callback(0.99, "Salvataggio EPUB...")

    lang_code = target_lang[:2].lower() if len(target_lang) > 2 else target_lang.lower()
    book.set_language(lang_code)

    epub.write_epub(output_path, book)
    logger.info(f"Translated EPUB saved to: {output_path}")

    if progress_callback:
        progress_callback(1.0, "Completato!")
