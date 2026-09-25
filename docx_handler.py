"""
ULTIMATE TRANSLATOR - DOCX Handler

Traduce documenti Word preservando stili di paragrafo, intestazioni, tabelle,
immagini, intestazioni/pie' di pagina e numerazione. Il testo viene sostituito
dentro il run esistente, cosi' font, corpo e stile restano quelli originali.
"""

import logging

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from translator import translate_blocks, estimate_tokens

logger = logging.getLogger(__name__)


def _is_translatable(text):
    return bool(text) and any(c.isalpha() for c in text)


def _iter_paragraphs(document):
    """Yield every paragraph in the document, tables and sections included."""
    seen = set()

    def walk_container(container):
        for para in getattr(container, "paragraphs", []):
            if id(para._p) not in seen:
                seen.add(id(para._p))
                yield para
        for table in getattr(container, "tables", []):
            for row in table.rows:
                for cell in row.cells:
                    for para in walk_container(cell):
                        yield para

    for para in walk_container(document):
        yield para

    for section in document.sections:
        for part in (section.header, section.footer,
                     section.even_page_header, section.even_page_footer,
                     section.first_page_header, section.first_page_footer):
            if part is None:
                continue
            for para in walk_container(part):
                yield para


def _set_paragraph_text(paragraph, text):
    """
    Replace a paragraph's text while keeping its formatting.

    The translated text goes into the first run (which carries font, size,
    bold/italic and colour); the remaining runs are emptied rather than
    deleted, so bookmarks and comment anchors attached to them survive.
    """
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for run in runs[1:]:
        run.text = ""


def extract_text_sample(input_path, max_chars=1000):
    document = docx.Document(input_path)
    out = []
    total = 0
    for para in _iter_paragraphs(document):
        t = para.text.strip()
        if _is_translatable(t):
            out.append(t)
            total += len(t)
            if total >= max_chars:
                break
    return " ".join(out)[:max_chars]


def analyze_docx(input_path):
    document = docx.Document(input_path)

    total_chars = 0
    total_words = 0
    headings = []
    all_text = []

    for para in _iter_paragraphs(document):
        text = para.text.strip()
        if not _is_translatable(text):
            continue
        total_chars += len(text)
        total_words += len(text.split())
        all_text.append(text)
        style = (para.style.name or "") if para.style is not None else ""
        if style.lower().startswith("heading") and len(headings) < 30:
            headings.append(text[:80])

    joined = " ".join(all_text)
    estimated_tokens = estimate_tokens(joined[:50000])
    if len(joined) > 50000:
        estimated_tokens = int(estimated_tokens * (len(joined) / 50000))

    return {
        "total_chars": total_chars,
        "total_words": total_words,
        "num_chapters": len(headings) or 1,
        "chapter_names": headings or ["Documento"],
        "estimated_tokens": estimated_tokens,
    }


def translate_docx(input_path, output_path, source_lang="English",
                   target_lang="Italian", provider="anthropic",
                   model="claude-sonnet-5", progress_callback=None):
    document = docx.Document(input_path)

    paragraphs = [p for p in _iter_paragraphs(document) if _is_translatable(p.text)]
    total = len(paragraphs)
    logger.info("DOCX: %d paragrafi da tradurre", total)

    if total == 0:
        document.save(output_path)
        if progress_callback:
            progress_callback(1.0, "Completato! (nessun testo trovato)")
        return

    def on_progress(done, tot):
        if progress_callback:
            progress_callback(min(done / max(tot, 1) * 0.97, 0.97),
                              "Tradotti %d/%d paragrafi" % (done, tot))

    translated = translate_blocks(
        [p.text for p in paragraphs],
        source_lang, target_lang, provider=provider, model=model,
        progress_callback=on_progress,
    )

    for para, text in zip(paragraphs, translated):
        try:
            _set_paragraph_text(para, text)
        except Exception as e:
            # Un paragrafo fallito non deve far perdere l'intero documento.
            logger.error("Paragrafo non riscritto: %s", e)

    if progress_callback:
        progress_callback(0.99, "Salvataggio DOCX...")

    document.save(output_path)
    logger.info("DOCX tradotto salvato in %s", output_path)

    if progress_callback:
        progress_callback(1.0, "Completato!")
