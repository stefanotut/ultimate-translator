"""
ULTIMATE TRANSLATOR - PDF Handler
Translates PDF files preserving layout, images, and formatting.
Supports both OpenAI and Anthropic providers.

Strategy:
- Group nearby text blocks that likely belong to the same paragraph
- Translate the groups of a page together, with the previous page as context
- Redact the original text and overlay the translation with the original
  color, size, weight and style, in fonts that cover every script
- Shrink the text when the translation needs more room than the original
"""

import os
import re
import html
import logging

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24.3
except ImportError:  # older PyMuPDF releases only have the fitz name
    import fitz

from translator import (
    DEFAULT_MODELS, FatalTranslationError, TranslationError, TranslationSession, estimate_tokens,
)

logger = logging.getLogger(__name__)

RTL_LANGUAGES = frozenset({'Arabic', 'Hebrew', 'Persian', 'Urdu'})
_SERIF_HINTS = ('times', 'serif', 'roman', 'georgia', 'garamond', 'minion', 'palatino', 'cambria',
                'baskerville', 'bookman', 'century', 'caslon', 'charter', 'didot', 'bodoni', 'antiqua')
_MONO_HINTS = ('courier', 'mono', 'consolas', 'menlo', 'inconsolata')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_translatable(text: str) -> bool:
    """Check if text has translatable alphabetic content."""
    if not text:
        return False
    stripped = text.strip()
    return bool(stripped) and any(c.isalpha() for c in stripped)


def _get_dominant_font(spans: list) -> tuple:
    """Get the most common font name and size from a list of spans."""
    if not spans:
        return "helv", 11
    best = max(spans, key=lambda s: len(s.get("text", "")))
    return best.get("font", "helv"), best.get("size", 11)


def _extract_color(span: dict) -> tuple:
    """Extract RGB color tuple from a span. Defaults to black."""
    c = span.get("color", 0)
    if isinstance(c, int):
        r = ((c >> 16) & 0xFF) / 255.0
        g = ((c >> 8) & 0xFF) / 255.0
        b = (c & 0xFF) / 255.0
        return (r, g, b)
    if isinstance(c, (tuple, list)) and len(c) >= 3:
        return tuple(c[:3])
    return (0, 0, 0)


def _join_lines(lines: list) -> str:
    """The lines of one text block as flowing text (the PDF broke them only to fit the page)."""
    text = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not text:
            text = line
        elif re.search(r"\w-$", text) and line[:1].islower():
            text = text[:-1] + line  # word hyphenated at the end of the line
        else:
            text += " " + line
    return text


def _group_nearby_blocks(blocks: list, vertical_threshold: float = 5.0) -> list:
    """
    Group text blocks that are vertically close together and share similar
    x-positions, suggesting they belong to the same paragraph or column.
    """
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: (b["bbox"].y0, b["bbox"].x0))

    groups = []
    current_group = {
        "blocks": [sorted_blocks[0]],
        "text": sorted_blocks[0]["text"],
        "bbox": fitz.Rect(sorted_blocks[0]["bbox"]),
        "spans": list(sorted_blocks[0]["spans"]),
        "justified": sorted_blocks[0].get("justified", False),
    }

    for i in range(1, len(sorted_blocks)):
        block = sorted_blocks[i]
        prev_bbox = current_group["bbox"]
        curr_bbox = block["bbox"]

        x_overlap = (
            abs(curr_bbox.x0 - prev_bbox.x0) < 50
            and abs(curr_bbox.x1 - prev_bbox.x1) < 50
        )
        y_close = curr_bbox.y0 - prev_bbox.y1 < vertical_threshold

        if x_overlap and y_close:
            current_group["blocks"].append(block)
            current_group["text"] += "\n" + block["text"]
            current_group["bbox"] = current_group["bbox"] | curr_bbox
            current_group["spans"].extend(block["spans"])
            current_group["justified"] = current_group["justified"] or block.get("justified", False)
        else:
            groups.append(current_group)
            current_group = {
                "blocks": [block],
                "text": block["text"],
                "bbox": fitz.Rect(block["bbox"]),
                "spans": list(block["spans"]),
                "justified": block.get("justified", False),
            }

    groups.append(current_group)
    return groups


def _is_justified(line_boxes: list, bbox) -> bool:
    """Paragraph whose lines (all but the last) reach both edges of the block."""
    full = line_boxes[:-1]
    if len(full) < 2:
        return False
    tolerance = max(2.0, bbox.width * 0.01)
    flush = sum(1 for box in full if box.x0 - bbox.x0 <= tolerance and bbox.x1 - box.x1 <= tolerance)
    return flush >= len(full) * 0.7


def _page_blocks(page) -> list:
    """Text blocks of a page with their text, box and spans."""
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    raw_blocks = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue

        lines = []
        line_boxes = []
        block_spans = []
        for line in block.get("lines", []):
            line_text = ""
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                line_text += span_text
                if _is_translatable(span_text):
                    block_spans.append(span)
            lines.append(line_text)
            if line_text.strip():
                line_boxes.append(fitz.Rect(line["bbox"]))

        block_text = _join_lines(lines)
        if not _is_translatable(block_text):
            continue

        bbox = fitz.Rect(block["bbox"])
        font_name, font_size = _get_dominant_font(block_spans)
        raw_blocks.append({
            "text": block_text,
            "bbox": bbox,
            "font_name": font_name,
            "font_size": font_size,
            "spans": block_spans,
            "justified": _is_justified(line_boxes, bbox),
        })
    return raw_blocks


def _css_for(group: dict, target_lang: str) -> str:
    """CSS reproducing the look of the original text (size, color, weight, style, family)."""
    spans = group["spans"]
    best = max(spans, key=lambda s: len(s.get("text", ""))) if spans else {}
    font_name, font_size = _get_dominant_font(spans)
    name = (font_name or "").lower()
    flags = best.get("flags", 0) or 0

    if flags & 8 or any(h in name for h in _MONO_HINTS):
        family = "monospace"
    elif "sans" not in name and (flags & 4 or any(h in name for h in _SERIF_HINTS)):
        family = "serif"
    else:
        family = "sans-serif"
    bold = bool(flags & 16) or any(h in name for h in ("bold", "black", "heavy", "semibold"))
    italic = bool(flags & 2) or "italic" in name or "oblique" in name
    r, g, b = _extract_color(best) if best else (0, 0, 0)
    rtl = target_lang in RTL_LANGUAGES
    align = "justify" if group.get("justified") else ("right" if rtl else "left")

    return (
        "* {"
        f"font-family: {family}; font-size: {max(float(font_size or 11), 4.0):.1f}px; "
        f"color: #{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}; line-height: 1.15; "
        f"font-weight: {'bold' if bold else 'normal'}; font-style: {'italic' if italic else 'normal'}; "
        f"direction: {'rtl' if rtl else 'ltr'}; text-align: {align}; "
        "margin: 0; padding: 0;"
        "}"
    )


def _insert_translation(page, group: dict, translated: str, target_lang: str):
    """Write the translation in the box of the original text, shrinking it if needed."""
    bbox = group["bbox"]
    try:
        body = html.escape(translated).replace("\n", "<br/>")
        page.insert_htmlbox(bbox, body, css=_css_for(group, target_lang), scale_low=0)
        return
    except Exception as e:
        logger.warning(f"insert_htmlbox failed, using the basic font: {e}")

    # Fallback: base-14 font (Latin text only)
    font_name, font_size = _get_dominant_font(group["spans"])
    color = _extract_color(group["spans"][0]) if group["spans"] else (0, 0, 0)
    current_size = font_size
    min_size = max(font_size * 0.55, 5.5)
    while current_size >= min_size:
        rc = page.insert_textbox(bbox, translated, fontsize=current_size, fontname="helv",
                                 color=color, align=fitz.TEXT_ALIGN_LEFT)
        if rc >= 0:
            return
        current_size -= 0.5


def extract_text_sample(input_path: str, max_chars: int = 1000) -> str:
    """Extract text sample from first page of PDF for language detection."""
    try:
        doc = fitz.open(input_path)
        if len(doc) == 0:
            return ""
        text = ""
        for page in doc:
            text += page.get_text("text")
            if len(text.strip()) >= max_chars:
                break
        doc.close()
        return text[:max_chars]
    except Exception as e:
        logger.warning(f"Failed to extract PDF text sample: {e}")
        return ""


# ---------------------------------------------------------------------------
# PDF analysis (for cost estimation)
# ---------------------------------------------------------------------------

def analyze_pdf(input_path: str) -> dict:
    """
    Analyze a PDF file and return statistics for cost estimation.

    Returns:
        dict with: total_chars, total_words, num_pages, estimated_tokens
    """
    doc = fitz.open(input_path)
    total_chars = 0
    total_words = 0
    all_text = ""

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        if text:
            total_chars += len(text)
            total_words += len(text.split())
            all_text += text + " "

    num_pages = len(doc)
    doc.close()

    estimated_tokens = estimate_tokens(all_text[:50000])
    if len(all_text) > 50000:
        ratio = len(all_text) / 50000
        estimated_tokens = int(estimated_tokens * ratio)

    return {
        "total_chars": total_chars,
        "total_words": total_words,
        "num_pages": num_pages,
        "estimated_tokens": estimated_tokens,
    }


# ---------------------------------------------------------------------------
# Main translation function
# ---------------------------------------------------------------------------

def translate_pdf(
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
    Translate a PDF file preserving layout and images.
    Groups nearby text blocks for more coherent translations.
    Raises TranslationError when nothing could be translated.
    """
    session = session or TranslationSession(source_lang, target_lang, model)
    try:
        doc = fitz.open(input_path)
    except Exception as e:
        raise TranslationError(f"PDF non leggibile (file danneggiato o non e un PDF): {e}") from e

    try:
        if not doc.is_pdf:
            raise TranslationError("Il file non e un PDF valido (forse un'immagine o una pagina web salvata come .pdf).")
        if doc.needs_pass:
            raise TranslationError("Questo PDF e protetto da password: rimuovi la password e riprova.")

        total_pages = len(doc)
        logger.info(f"PDF has {total_pages} pages to translate")
        if total_pages == 0:
            raise TranslationError("Questo PDF non ha pagine.")

        previous_translated = ""
        text_groups = 0
        failed_pages = []

        for page_num in range(total_pages):
            page = doc[page_num]
            logger.info(f"  Translating page {page_num + 1}/{total_pages}")

            if progress_callback:
                progress_callback(
                    page_num / total_pages,
                    f"Pagina {page_num + 1}/{total_pages} - Analisi layout..."
                )

            try:
                grouped = _group_nearby_blocks(_page_blocks(page))
                if not grouped:
                    logger.info(f"  Page {page_num + 1}: no translatable text")
                    continue
                text_groups += len(grouped)

                if progress_callback:
                    progress_callback(
                        page_num / total_pages + 0.3 / total_pages,
                        f"Pagina {page_num + 1}/{total_pages} - Traduzione {len(grouped)} blocchi..."
                    )

                def page_progress(done, total, _page_num=page_num):
                    if progress_callback:
                        progress_callback(
                            min((_page_num + done / max(total, 1)) / total_pages, 0.99),
                            f"Pagina {_page_num + 1}/{total_pages} - Blocco {done}/{total}"
                        )

                sources = [group["text"] for group in grouped]
                translated = session.translate_batch(sources, previous_translated, on_progress=page_progress)
                changed = [(group, value) for group, source, value in zip(grouped, sources, translated)
                           if value != source]
                if not changed:
                    continue  # nothing translated here: the page keeps its original text
                previous_translated = "\n".join(value for _, value in changed)[-800:]

                for group, _ in changed:
                    page.add_redact_annot(group["bbox"])
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

                for group, value in changed:
                    _insert_translation(page, group, value, target_lang)
            except (FatalTranslationError, TranslationError):
                raise
            except Exception as e:
                logger.exception(f"Page {page_num + 1} could not be translated")
                failed_pages.append(page_num + 1)
                if progress_callback:
                    progress_callback(
                        (page_num + 1) / total_pages,
                        f"Pagina {page_num + 1}/{total_pages} - ERRORE: {str(e)[:60]}"
                    )

        if text_groups == 0:
            raise TranslationError(
                "Questo PDF non contiene testo selezionabile (sembra una scansione o solo immagini): "
                "serve prima il riconoscimento del testo (OCR)."
            )
        if session.passages == session.untranslated:
            raise TranslationError(
                "Nessuna parte del documento e stata tradotta: il modello ha rifiutato tutte le sezioni. "
                "Prova con un altro modello."
            )

        if progress_callback:
            progress_callback(0.99, "Salvataggio PDF...")

        partial = output_path + ".part"
        try:
            doc.save(partial, garbage=4, deflate=True, clean=True)
            os.replace(partial, output_path)
        finally:
            if os.path.exists(partial):
                os.remove(partial)
    finally:
        doc.close()

    logger.info(f"Translated PDF saved to: {output_path}")

    if progress_callback:
        progress_callback(1.0, "Completato!")
    return {"failed_pages": len(failed_pages)}
