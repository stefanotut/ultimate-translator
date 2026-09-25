"""
ULTIMATE TRANSLATOR - TXT / Markdown Handler

Traduce testo semplice mantenendo la struttura a paragrafi e le righe vuote.
Per il Markdown la sintassi (#, *, elenchi, link) viene preservata dal prompt
di traduzione, che tratta il contenuto come testo formattato da non alterare.
"""

import io
import logging

from translator import translate_blocks, estimate_tokens

logger = logging.getLogger(__name__)

# Paragrafi accorpati fino a questa dimensione prima di chiamare l'AI:
# meno chiamate, piu' contesto, costo minore.
BATCH_CHARS = 2500


def _read(input_path):
    with io.open(input_path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _is_translatable(text):
    return bool(text) and any(c.isalpha() for c in text)


def extract_text_sample(input_path, max_chars=1000):
    return _read(input_path)[:max_chars]


def analyze_txt(input_path):
    text = _read(input_path)
    paragraphs = [p for p in text.split("\n\n") if _is_translatable(p)]

    estimated_tokens = estimate_tokens(text[:50000])
    if len(text) > 50000:
        estimated_tokens = int(estimated_tokens * (len(text) / 50000))

    return {
        "total_chars": len(text),
        "total_words": len(text.split()),
        "num_chapters": max(len(paragraphs), 1),
        "chapter_names": [p.strip()[:80] for p in paragraphs[:30]] or ["Testo"],
        "estimated_tokens": estimated_tokens,
    }


def translate_txt(input_path, output_path, source_lang="English",
                  target_lang="Italian", provider="anthropic",
                  model="claude-sonnet-5", progress_callback=None):
    text = _read(input_path)

    # Si divide sui doppi a-capo e si ricompone con lo stesso separatore,
    # cosi' l'impaginazione del file resta identica.
    parts = text.split("\n\n")

    batches = []
    current = []
    size = 0
    for part in parts:
        current.append(part)
        size += len(part) + 2
        if size >= BATCH_CHARS:
            batches.append(current)
            current, size = [], 0
    if current:
        batches.append(current)

    total = len(batches)
    if total == 0:
        with io.open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        if progress_callback:
            progress_callback(1.0, "Completato! (file vuoto)")
        return

    def on_progress(done, tot):
        if progress_callback:
            progress_callback(min(done / max(tot, 1) * 0.97, 0.97),
                              "Tradotti %d/%d blocchi" % (done, tot))

    out_batches = translate_blocks(
        ["\n\n".join(b) for b in batches],
        source_lang, target_lang, provider=provider, model=model,
        progress_callback=on_progress,
    )

    with io.open(output_path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(out_batches))

    logger.info("TXT tradotto salvato in %s", output_path)
    if progress_callback:
        progress_callback(1.0, "Completato!")
