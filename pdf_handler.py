"""
ULTIMATE TRANSLATOR - PDF Handler
Translates PDF files preserving layout, images, and formatting.
Supports both OpenAI and Anthropic providers.

Strategy:
- Group nearby text blocks that likely belong to the same paragraph
- Translate grouped blocks with context awareness
- Redact original text and overlay translated text preserving colors and fonts
- Smart font size reduction to fit translated text in the same bounding box
"""

import os
import logging
import fitz  # PyMuPDF
from translator import translate_text, estimate_tokens

logger = logging.getLogger(__name__)


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
        else:
            groups.append(current_group)
            current_group = {
                "blocks": [block],
                "text": block["text"],
                "bbox": fitz.Rect(block["bbox"]),
                "spans": list(block["spans"]),
            }

    groups.append(current_group)
    return groups


def extract_text_sample(input_path: str, max_chars: int = 1000) -> str:
    """Extract text sample from first page of PDF for language detection."""
    try:
        doc = fitz.open(input_path)
        if len(doc) == 0:
            return ""
        text = doc[0].get_text("text")
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
    model: str = "claude-sonnet-4-20250514",
    progress_callback=None,
):
    """
    Translate a PDF file preserving layout and images.
    Groups nearby text blocks for more coherent translations.
    """
    doc = fitz.open(input_path)
    total_pages = len(doc)

    logger.info(f"PDF has {total_pages} pages to translate")

    if total_pages == 0:
        logger.warning("PDF has no pages")
        doc.save(output_path)
        doc.close()
        if progress_callback:
            progress_callback(1.0, "Completato! (PDF vuoto)")
        return

    previous_translated = ""

    for page_num in range(total_pages):
        page = doc[page_num]

        logger.info(f"  Translating page {page_num + 1}/{total_pages}")

        if progress_callback:
            progress = page_num / total_pages
            progress_callback(
                progress,
                f"Pagina {page_num + 1}/{total_pages} - Analisi layout..."
            )

        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        raw_blocks = []

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue

            block_text = ""
            block_spans = []

            for line in block.get("lines", []):
                line_text = ""
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    line_text += span_text
                    if _is_translatable(span_text):
                        block_spans.append(span)
                block_text += line_text + "\n"

            block_text = block_text.strip()
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
            })

        if not raw_blocks:
            logger.info(f"  Page {page_num + 1}: no translatable text")
            continue

        grouped = _group_nearby_blocks(raw_blocks)

        if progress_callback:
            progress_callback(
                page_num / total_pages + 0.3 / total_pages,
                f"Pagina {page_num + 1}/{total_pages} - Traduzione {len(grouped)} blocchi..."
            )

        for i, group in enumerate(grouped):
            try:
                translated = translate_text(
                    group["text"],
                    source_lang,
                    target_lang,
                    provider=provider,
                    model=model,
                    previous_context=previous_translated,
                )
                group["translated"] = translated
                previous_translated = translated
            except Exception as e:
                logger.error(f"Failed to translate block on page {page_num + 1}: {e}")
                group["translated"] = group["text"]

            if progress_callback:
                block_progress = (page_num + (i + 1) / len(grouped)) / total_pages
                progress_callback(
                    min(block_progress, 0.99),
                    f"Pagina {page_num + 1}/{total_pages} - Blocco {i + 1}/{len(grouped)}"
                )

        for group in grouped:
            bbox = group["bbox"]
            page.add_redact_annot(bbox)

        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        for group in grouped:
            bbox = group["bbox"]
            translated = group["translated"]

            font_name, font_size = _get_dominant_font(group["spans"])

            color = (0, 0, 0)
            if group["spans"]:
                color = _extract_color(group["spans"][0])

            fontname = "helv"

            rc = -1
            current_size = font_size
            min_size = max(font_size * 0.55, 5.5)

            while current_size >= min_size:
                rc = page.insert_textbox(
                    bbox,
                    translated,
                    fontsize=current_size,
                    fontname=fontname,
                    color=color,
                    align=fitz.TEXT_ALIGN_LEFT,
                )
                if rc >= 0:
                    break
                current_size -= 0.5

            if rc < 0:
                page.insert_textbox(
                    bbox,
                    translated,
                    fontsize=min_size,
                    fontname=fontname,
                    color=color,
                    align=fitz.TEXT_ALIGN_LEFT,
                )

    if progress_callback:
        progress_callback(0.99, "Salvataggio PDF...")

    doc.save(output_path, garbage=4, deflate=True, clean=True)
    doc.close()

    logger.info(f"Translated PDF saved to: {output_path}")

    if progress_callback:
        progress_callback(1.0, "Completato!")
