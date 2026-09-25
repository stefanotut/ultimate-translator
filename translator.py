"""
ULTIMATE TRANSLATOR - AI Translation Engine
Supports both OpenAI and Anthropic providers.
Context-aware chunking for coherent book-length translations.
"""

import os
import re
import time
import logging
import threading
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-initialised clients
# ---------------------------------------------------------------------------

_anthropic_client = None
_openai_client = None


def get_anthropic_client():
    """Initialize and return the Anthropic client."""
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key or api_key.startswith("sk-ant-xxxx"):
            raise ValueError(
                "Chiave API Anthropic non configurata. "
                "Modifica il file .env e inserisci la tua ANTHROPIC_API_KEY."
            )
        _anthropic_client = Anthropic(api_key=api_key)
    return _anthropic_client


def get_openai_client():
    """Initialize and return the OpenAI client."""
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key.startswith("sk-xxxx"):
            raise ValueError(
                "Chiave API OpenAI non configurata. "
                "Modifica il file .env e inserisci la tua OPENAI_API_KEY."
            )
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


# ---------------------------------------------------------------------------
# Model pricing dictionary (per 1M tokens)
# ---------------------------------------------------------------------------

MODEL_PRICING = {
    # =====================================================
    # OPENAI MODELS
    # =====================================================
    "gpt-5.2": {
        "provider": "openai",
        "display_name": "GPT-5.2",
        "input_cost": 5.00,
        "output_cost": 20.00,
        "quality": "very_high",
        "speed": "fast",
        "cost_indicator": "$$$",
        "quality_badge": "Ultimo Modello",
        "description": "Il modello piu avanzato di OpenAI",
    },
    "gpt-4.1": {
        "provider": "openai",
        "display_name": "GPT-4.1",
        "input_cost": 2.00,
        "output_cost": 8.00,
        "quality": "high",
        "speed": "fast",
        "cost_indicator": "$$",
        "quality_badge": "Bilanciato",
        "description": "Ottimo rapporto qualita/prezzo",
    },
    "gpt-4.1-mini": {
        "provider": "openai",
        "display_name": "GPT-4.1 Mini",
        "input_cost": 0.40,
        "output_cost": 1.60,
        "quality": "good",
        "speed": "very_fast",
        "cost_indicator": "$",
        "quality_badge": "Budget",
        "description": "Veloce e economico",
    },
    "gpt-4.1-nano": {
        "provider": "openai",
        "display_name": "GPT-4.1 Nano",
        "input_cost": 0.10,
        "output_cost": 0.40,
        "quality": "basic",
        "speed": "very_fast",
        "cost_indicator": "$",
        "quality_badge": "Ultra Budget",
        "description": "Il piu economico di OpenAI",
    },
    "gpt-4o": {
        "provider": "openai",
        "display_name": "GPT-4o",
        "input_cost": 2.50,
        "output_cost": 10.00,
        "quality": "high",
        "speed": "fast",
        "cost_indicator": "$$",
        "quality_badge": "Popolare",
        "description": "Modello multimodale affidabile",
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "display_name": "GPT-4o Mini",
        "input_cost": 0.15,
        "output_cost": 0.60,
        "quality": "good",
        "speed": "very_fast",
        "cost_indicator": "$",
        "quality_badge": "Budget",
        "description": "Veloce e molto economico",
    },
    "o4-mini": {
        "provider": "openai",
        "display_name": "o4-mini",
        "input_cost": 1.10,
        "output_cost": 4.40,
        "quality": "high",
        "speed": "fast",
        "cost_indicator": "$$",
        "quality_badge": "Ragionamento",
        "description": "Reasoning veloce",
    },
    "o3": {
        "provider": "openai",
        "display_name": "o3",
        "input_cost": 10.00,
        "output_cost": 40.00,
        "quality": "very_high",
        "speed": "slow",
        "cost_indicator": "$$$$",
        "quality_badge": "Deep Reasoning",
        "description": "Ragionamento avanzato",
    },
    "o3-mini": {
        "provider": "openai",
        "display_name": "o3-mini",
        "input_cost": 1.10,
        "output_cost": 4.40,
        "quality": "good",
        "speed": "fast",
        "cost_indicator": "$$",
        "quality_badge": "Veloce",
        "description": "Reasoning compatto",
    },
    "o1": {
        "provider": "openai",
        "display_name": "o1",
        "input_cost": 15.00,
        "output_cost": 60.00,
        "quality": "very_high",
        "speed": "slow",
        "cost_indicator": "$$$$",
        "quality_badge": "Ragionamento",
        "description": "Reasoning originale di OpenAI",
    },
    # =====================================================
    # ANTHROPIC MODELS
    # =====================================================
    "claude-opus-5": {
        "provider": "anthropic",
        "display_name": "Claude Opus 5",
        "input_cost": 5.00,
        "output_cost": 25.00,
        "quality": "very_high",
        "speed": "slow",
        "cost_indicator": "$$$",
        "quality_badge": "Miglior Qualita",
        "description": "Il modello piu potente di Anthropic",
    },
    "claude-sonnet-5": {
        "provider": "anthropic",
        "display_name": "Claude Sonnet 5",
        "input_cost": 3.00,
        "output_cost": 15.00,
        "quality": "very_high",
        "speed": "fast",
        "cost_indicator": "$$",
        "quality_badge": "Consigliato",
        "description": "Qualita quasi Opus a costo Sonnet",
    },
    "claude-haiku-4-5": {
        "provider": "anthropic",
        "display_name": "Claude Haiku 4.5",
        "input_cost": 1.00,
        "output_cost": 5.00,
        "quality": "good",
        "speed": "very_fast",
        "cost_indicator": "$",
        "quality_badge": "Veloce",
        "description": "Velocissimo e economico",
    },
}

# Modelli Anthropic che rifiutano i parametri di sampling (400 su temperature).
ANTHROPIC_NO_SAMPLING = {"claude-opus-5", "claude-sonnet-5"}

# Col thinking disabilitato i modelli Claude 5 possono, di rado, far trapelare
# un blocco <thinking> nel testo visibile. In un libro sarebbe corruzione
# visibile, quindi si toglie qui: non e' un tag HTML valido, l'EPUB non rischia.
_REASONING_LEAK_RE = re.compile(
    r'^\s*<(thinking|antml:thinking)\b[^>]*>.*?</\1>\s*',
    re.DOTALL | re.IGNORECASE,
)


# Frasi con cui un modello risponde A TE invece di tradurre. Succede sui blocchi
# molto corti mandati da soli — un titolo di due parole sembra una richiesta
# monca, e il modello chiede il testo completo. Quella risposta finiva STAMPATA
# nel libro: e' successo davvero, su un libro illustrato, al posto del titolo di
# un capitolo.
_RISPOSTE_A_ME = (
    "ho bisogno del testo", "non hai fornito", "potresti", "per favore incolla",
    "il messaggio contiene solo", "sarò lieto", "saro lieto", "il testo sorgente",
    "sembra incompleto", "sembra troncato", "fornisci il testo",
    "i need the text", "please provide", "could you please", "it seems the text",
    "you haven't provided", "i'd be happy to",
    # misurate sui titoli corti delle pagine scansionate ("Neuron 1" -> ...)
    "sono pronto", "inviami", "non posso aiutart", "non ho ricevuto", "i'm ready",
    "please send", "i can't help", "i cannot help",
)


def _e_una_risposta_a_me(sorgente: str, tradotto: str) -> bool:
    """Vero se il modello ha risposto a noi invece di tradurre.

    Due segnali insieme, perche' uno solo darebbe falsi allarmi: il testo
    contiene una formula da conversazione, ED e' sproporzionato rispetto
    all'originale (un titolo di 20 caratteri non diventa un paragrafo di 200).
    """
    t = (tradotto or "").strip().lower()
    if not t:
        return False
    if not any(f in t for f in _RISPOSTE_A_ME):
        return False
    s = (sorgente or "").strip()
    # Per una sorgente corta ("Neuron 1") anche una risposta corta e' sospetta:
    # il pavimento di 60 caratteri vale solo per i paragrafi veri.
    return len(t) > max(60 if len(s) >= 40 else 0, len(s) * 2.5)


def _strip_reasoning_leak(text: str) -> str:
    """Remove a leaked reasoning block from the start of a translation."""
    return _REASONING_LEAK_RE.sub('', text, count=1)


# ---------------------------------------------------------------------------
# Contesto del job: abilita la cache per blocco, cosi' una traduzione ripresa
# dopo un crash non ripaga l'AI per i blocchi gia' tradotti.
# ---------------------------------------------------------------------------

_job_ctx = threading.local()


def set_job_context(job_id):
    """Bind this thread's translations to a job's resume cache."""
    _job_ctx.job_id = job_id


def clear_job_context():
    _job_ctx.job_id = None


def _current_job():
    return getattr(_job_ctx, "job_id", None)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str, model: str = "gpt-4o") -> int:
    """
    Estimate the number of tokens in text.
    Uses tiktoken for OpenAI models, char/4 approximation for Anthropic.
    """
    if not text:
        return 0

    provider = MODEL_PRICING.get(model, {}).get("provider", "anthropic")

    if provider == "openai":
        try:
            import tiktoken
            try:
                enc = tiktoken.encoding_for_model(model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass

    # Fallback / Anthropic approximation
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Translation system prompt - optimised for literary quality
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """You are a world-class literary translator, renowned for producing translations that read as if they were originally written in the target language.

SOURCE LANGUAGE: {source_lang}
TARGET LANGUAGE: {target_lang}

YOUR TRANSLATION PHILOSOPHY:
- Capture the SOUL of the text, not just the words. A great translation makes the reader forget they are reading a translation.
- Preserve the author's unique voice, rhythm, cadence, and register with absolute fidelity.
- Render idiomatic expressions into equally vivid, natural idioms in {target_lang} — never translate idioms literally.
- Maintain the emotional undertone: humor stays funny, tension stays taut, poetry stays lyrical.
- Adapt cultural references when necessary so {target_lang} readers feel the same impact as the original audience.
- Use punctuation, sentence length, and paragraph rhythm that feel native to {target_lang} literary conventions.

STRICT RULES:
1. Return ONLY the translated text — no commentary, no notes, no explanations, no code blocks, no quotation wrappers.
2. Preserve ALL HTML/XML tags, attributes, classes, and IDs exactly as they appear. Translate only the human-readable text content.
3. Preserve proper nouns (people, brands, places) unless they have a well-established translation in {target_lang}.
4. Preserve technical terms, code snippets, URLs, email addresses, and numbers exactly.
5. Maintain the exact same paragraph structure, line breaks, and spacing.
6. Do NOT add, remove, skip, or summarise any content.
7. Do NOT wrap your output in markdown code fences or quotation marks."""


CONTEXT_ADDENDUM = """

CONTEXT FROM PREVIOUS SECTION (for coherence — do NOT re-translate this, it is provided only so you maintain consistency of terminology, tone, and narrative flow):
---
{previous_context}
---

Now translate the following new section, continuing naturally from the context above."""


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def detect_language(text_sample: str, provider: str = "anthropic", model: str = "claude-sonnet-5") -> str:
    """Use an LLM to detect the language of a text sample."""
    if not text_sample or not text_sample.strip():
        return "Unknown"

    prompt_text = (
        "Detect the language of the following text. "
        "Reply with ONLY the language name in English "
        "(e.g. 'English', 'Italian', 'French', 'German', 'Spanish', etc.). "
        "Nothing else.\n\n"
        f"{text_sample[:1000]}"
    )

    try:
        if provider == "openai":
            cl = get_openai_client()
            response = cl.chat.completions.create(
                model=model,
                max_tokens=50,
                messages=[{"role": "user", "content": prompt_text}],
            )
            detected = response.choices[0].message.content.strip().strip(".'\"")
        else:
            cl = get_anthropic_client()
            response = cl.messages.create(
                model=model,
                max_tokens=50,
                messages=[{"role": "user", "content": prompt_text}],
            )
            detected = response.content[0].text.strip().strip(".'\"")

        detected = detected.split("\n")[0].strip()
        logger.info(f"Language detected: {detected}")
        return detected
    except Exception as e:
        logger.warning(f"Language detection failed: {e}")
        return "Unknown"


# ---------------------------------------------------------------------------
# Motore a lotti paralleli
#
# Tradurre un paragrafo per chiamata costava ~4.8 s a blocco: quasi tutto
# andata/ritorno di rete, non traduzione. Qui i blocchi vengono raggruppati in
# una sola chiamata (meno viaggi) e i gruppi partono in parallelo (viaggi
# sovrapposti). In piu' il modello vede 15-20 paragrafi consecutivi invece di
# uno solo: il contesto migliora, non peggiora.
# ---------------------------------------------------------------------------

BATCH_MAX_CHARS = int(os.environ.get("BATCH_MAX_CHARS", "3500"))
BATCH_MAX_BLOCKS = int(os.environ.get("BATCH_MAX_BLOCKS", "20"))

# Tetto globale alle richieste AI in volo, condiviso da tutti i libri in
# traduzione: protegge dai rate limit dei provider quando piu' job girano.
API_CONCURRENCY = int(os.environ.get("API_CONCURRENCY", "8"))
_api_sem = threading.Semaphore(API_CONCURRENCY)

_MARK = "⟦%d⟧"
_MARK_RE = re.compile(r"⟦(\d+)⟧")

BATCH_SYSTEM_SUFFIX = """

FORMATO DI QUESTA RICHIESTA
Ricevi piu' segmenti numerati, ciascuno introdotto da un marcatore ⟦N⟧ su una riga a se'.
Regole assolute:
- Riproduci ESATTAMENTE gli stessi marcatori ⟦N⟧, nello stesso ordine, uno per riga.
- Traduci solo il contenuto fra un marcatore e il successivo.
- Non unire, non dividere, non riordinare e non omettere alcun segmento.
- Non aggiungere marcatori che non erano presenti.
- Nessun commento, nessuna introduzione, nessuna spiegazione."""


def _batch_prompt(blocks):
    return "\n".join("%s\n%s" % (_MARK % i, b) for i, b in enumerate(blocks))


def _parse_batch(reply, expected):
    """
    Split a marked reply back into segments.

    Returns None if the model didn't return exactly the expected markers, so
    the caller can fall back to one-by-one translation instead of writing
    scrambled text into the book.
    """
    positions = [(m.start(), m.end(), int(m.group(1))) for m in _MARK_RE.finditer(reply)]
    if len(positions) != expected:
        return None
    if [p[2] for p in positions] != list(range(expected)):
        return None

    out = []
    for idx, (_start, end, _n) in enumerate(positions):
        stop = positions[idx + 1][0] if idx + 1 < len(positions) else len(reply)
        out.append(reply[end:stop].strip("\n").strip())
    return out


_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)")


def _tag_signature(html):
    """Multiset of tag names in a fragment, for before/after comparison."""
    from collections import Counter
    return Counter("%s%s" % (slash, name.lower())
                   for slash, name in _TAG_RE.findall(html or ""))


def _repair_lost_tags(sources, translations, source_lang, target_lang,
                      provider, model):
    """
    Re-translate blocks whose inline tags didn't survive.

    Il modello ogni tanto lascia cadere un <em> o uno <strong>: su un libro
    intero sono poche unita' su centinaia, ma sono corsivi che spariscono
    dalla pagina. Qui si ritraducono solo quei blocchi, uno alla volta.
    """
    for i, src in enumerate(sources):
        if "<" not in src:
            continue
        want = _tag_signature(src)
        if not want or want == _tag_signature(translations[i]):
            continue
        try:
            retry = translate_text(src, source_lang, target_lang,
                                   provider=provider, model=model,
                                   max_retries=2, use_cache=False)
            if _tag_signature(retry) == want:
                translations[i] = retry
                logger.info("Tag recuperati su un blocco al secondo tentativo")
            else:
                logger.warning("Blocco con formattazione incompleta dopo il ritentativo")
        except Exception as e:
            logger.warning("Ritentativo formattazione fallito: %s", str(e)[:100])
    return translations


def _translate_batch_call(blocks, source_lang, target_lang, provider, model,
                          max_retries=3):
    """One API call for many blocks. Falls back to per-block on any mismatch."""
    if len(blocks) == 1:
        # Un blocco solo, senza segnaposto: davanti a un'etichetta corta il
        # modello a volte RISPONDE invece di tradurre ("Neuron 1" -> "Non posso
        # aiutarti"). Misurato su pagine scansionate: la guardia serve anche qui.
        solo = translate_text(blocks[0], source_lang, target_lang,
                              provider=provider, model=model,
                              max_retries=max_retries)
        if _e_una_risposta_a_me(blocks[0], solo):
            logger.warning("Il modello ha risposto invece di tradurre (%r): tengo l'originale", blocks[0][:60])
            return [blocks[0]]
        return _repair_lost_tags([blocks[0]], [solo], source_lang, target_lang, provider, model)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        source_lang=source_lang, target_lang=target_lang) + BATCH_SYSTEM_SUFFIX

    try:
        reply = _call_model(system_prompt, _batch_prompt(blocks), provider, model,
                            max_retries=max_retries, max_tokens=16000)
        parsed = _parse_batch(reply, len(blocks))
        if parsed is not None and all(p.strip() for p in parsed):
            parsed = [_strip_reasoning_leak(p) for p in parsed]
            return _repair_lost_tags(blocks, parsed, source_lang, target_lang,
                                     provider, model)
        logger.warning("Lotto non conforme (%d blocchi): ripiego uno per uno",
                       len(blocks))
    except Exception as e:
        logger.warning("Lotto fallito (%s): ripiego uno per uno", str(e)[:120])

    fuori = []
    for b in blocks:
        t = translate_text(b, source_lang, target_lang, provider=provider,
                           model=model, max_retries=max_retries)
        if _e_una_risposta_a_me(b, t):
            # Meglio il titolo in inglese che una frase del modello stampata
            # dentro il libro. Si tiene l'originale e si segnala.
            logger.warning("Il modello ha risposto invece di tradurre (%r): "
                           "tengo l'originale", b[:60])
            t = b
        fuori.append(t)
    return fuori


def translate_blocks(blocks, source_lang="English", target_lang="Italian",
                     provider="anthropic", model="claude-sonnet-5",
                     progress_callback=None, should_stop=None):
    """
    Translate a list of text/HTML blocks, preserving order.

    Cached blocks are skipped, the rest are grouped and sent in parallel.
    `progress_callback(done, total)` is called as work lands;
    `should_stop()` lets the caller cancel between batches.
    """
    import store
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(blocks)
    results = [None] * total
    job_id = _current_job()

    # 1) Riuso di quanto gia' tradotto (ripresa dopo crash, o frasi ripetute).
    todo = []
    for i, block in enumerate(blocks):
        if not block or not block.strip():
            results[i] = block
            continue
        if job_id:
            key = store.cache_key(block, model, source_lang, target_lang)
            hit = store.cache_get(job_id, key)
            if hit is not None:
                results[i] = hit
                continue
        todo.append(i)

    done = total - len(todo)
    if progress_callback:
        progress_callback(done, total)

    # 2) Raggruppamento per dimensione: pochi viaggi invece di tanti.
    batches, current, size = [], [], 0
    for i in todo:
        blen = len(blocks[i])
        if current and (size + blen > BATCH_MAX_CHARS or len(current) >= BATCH_MAX_BLOCKS):
            batches.append(current)
            current, size = [], 0
        current.append(i)
        size += blen
    if current:
        batches.append(current)

    if not batches:
        return results

    logger.info("%d blocchi da tradurre in %d lotti (%d gia' in cache)",
                len(todo), len(batches), done)

    # 3) Invio in parallelo, con tetto globale alle richieste in volo.
    lock = threading.Lock()
    counter = [done]
    stopped = threading.Event()

    def run(batch):
        if stopped.is_set():
            return batch, None
        with _api_sem:
            if stopped.is_set():
                return batch, None
            texts = [blocks[i] for i in batch]
            return batch, _translate_batch_call(texts, source_lang, target_lang,
                                                provider, model)

    workers = max(1, min(API_CONCURRENCY, len(batches)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, b) for b in batches]
        try:
            for fut in as_completed(futures):
                batch, translated = fut.result()
                if translated is None:
                    continue
                for idx, i in enumerate(batch):
                    results[i] = translated[idx]
                    if job_id:
                        store.cache_put(
                            job_id,
                            store.cache_key(blocks[i], model, source_lang, target_lang),
                            translated[idx])
                with lock:
                    counter[0] += len(batch)
                    if progress_callback:
                        progress_callback(counter[0], total)
                if should_stop and should_stop():
                    stopped.set()
                    for f in futures:
                        f.cancel()
                    raise _Stopped()
        except _Stopped:
            raise
        except Exception:
            stopped.set()
            raise

    # Un lotto annullato lascia dei None: il testo originale e' meglio del vuoto.
    for i in range(total):
        if results[i] is None:
            results[i] = blocks[i]
    return results


class _Stopped(Exception):
    """Internal: propagates a cancellation out of the pool."""


# Modelli OpenAI che vogliono max_completion_tokens al posto di max_tokens.
NEEDS_MAX_COMPLETION_TOKENS = {"gpt-5.2", "o1", "o3", "o3-mini", "o4-mini"}


def _call_model(system_prompt, user_message, provider, model,
                max_retries=3, max_tokens=8192):
    """
    Una chiamata al modello, con retry a backoff esponenziale.

    Unico punto in cui si parla con i provider: le stranezze per modello
    (temperature rifiutata dai Claude 5, max_completion_tokens su alcuni
    OpenAI, blocco thinking da saltare) stanno tutte qui.
    """
    for attempt in range(max_retries):
        try:
            if provider == "openai":
                kwargs = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.3,
                }
                if model in NEEDS_MAX_COMPLETION_TOKENS:
                    kwargs["max_completion_tokens"] = max_tokens
                else:
                    kwargs["max_tokens"] = max_tokens
                response = get_openai_client().chat.completions.create(**kwargs)
                return response.choices[0].message.content

            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
                # Il reasoning non serve a tradurre e verrebbe fatturato come
                # output, sballando la stima di costo mostrata all'utente.
                "thinking": {"type": "disabled"},
            }
            # I modelli Claude 5 rispondono 400 se ricevono temperature.
            if model not in ANTHROPIC_NO_SAMPLING:
                kwargs["temperature"] = 0.3

            response = get_anthropic_client().messages.create(**kwargs)
            # Con il thinking attivo il primo blocco non e' testo: cercare
            # sempre il blocco text invece di indicizzare content[0].
            return next((b.text for b in response.content if b.type == "text"), None)

        except Exception as e:
            if attempt >= max_retries - 1:
                logger.error("Traduzione fallita dopo %d tentativi: %s", max_retries, e)
                raise
            wait = 2 ** attempt
            logger.warning("Tentativo %d/%d fallito (%s). Riprovo fra %ds...",
                           attempt + 1, max_retries, str(e)[:120], wait)
            time.sleep(wait)


# ---------------------------------------------------------------------------
# Core translation function
# ---------------------------------------------------------------------------

def translate_text(
    text: str,
    source_lang: str = "English",
    target_lang: str = "Italian",
    provider: str = "anthropic",
    model: str = "claude-sonnet-5",
    previous_context: str = "",
    max_retries: int = 3,
    use_cache: bool = True,
) -> str:
    """
    Translate a chunk of text using the specified provider and model.

    Args:
        text: The text/HTML to translate.
        source_lang: Source language name.
        target_lang: Target language name.
        provider: 'openai' or 'anthropic'.
        model: The model identifier.
        previous_context: The translated text of the previous chunk (for coherence).
        max_retries: Number of retry attempts with exponential backoff.

    Returns:
        Translated text.
    """
    if not text or not text.strip():
        return text

    # Skip text that is only whitespace, numbers, or punctuation
    stripped = text.strip()
    if all(
        c.isdigit()
        or c in '.,;:!?@#$%^&*()[]{}/<>\\|=-_+~`\'" \t\n\r'
        or c.isspace()
        for c in stripped
    ):
        return text

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        source_lang=source_lang,
        target_lang=target_lang,
    )

    user_message = text
    if previous_context:
        ctx = previous_context[-800:] if len(previous_context) > 800 else previous_context
        user_message = (
            CONTEXT_ADDENDUM.format(previous_context=ctx)
            + "\n\n"
            + text
        )

    # Cache di ripresa: se questo identico blocco e' gia' stato tradotto per
    # questo job (prima che il processo morisse) lo riusiamo senza richiamare
    # l'AI. E' cio' che rende un restart gratuito invece che da ripagare.
    job_id = _current_job() if use_cache else None
    ck = None
    if job_id:
        import store
        ck = store.cache_key(text, model, source_lang, target_lang)
        cached = store.cache_get(job_id, ck)
        if cached is not None:
            return cached

    translated = _call_model(system_prompt, user_message, provider, model,
                             max_retries=max_retries)
    if not translated:
        return text

    result = _strip_reasoning_leak(translated).strip()
    if job_id and ck:
        import store
        store.cache_put(job_id, ck, result)
    return result


# ---------------------------------------------------------------------------
# Smart chunking utilities
# ---------------------------------------------------------------------------

def _split_at_paragraph_boundaries(text: str, chunk_size: int = 2500) -> list:
    """
    Split text into chunks at paragraph boundaries.
    Never splits mid-sentence.
    """
    if len(text) <= chunk_size:
        return [text]

    paragraphs = re.split(r'(\n\s*\n)', text)

    chunks = []
    current_chunk = ""

    for part in paragraphs:
        if len(current_chunk) + len(part) > chunk_size and current_chunk.strip():
            chunks.append(current_chunk)
            current_chunk = part
        else:
            current_chunk += part

    if current_chunk.strip():
        chunks.append(current_chunk)

    refined = []
    for chunk in chunks:
        if len(chunk) <= chunk_size * 1.5:
            refined.append(chunk)
        else:
            lines = chunk.split('\n')
            sub_chunk = ""
            for line in lines:
                if len(sub_chunk) + len(line) + 1 > chunk_size and sub_chunk.strip():
                    refined.append(sub_chunk)
                    sub_chunk = line
                else:
                    sub_chunk = sub_chunk + '\n' + line if sub_chunk else line
            if sub_chunk.strip():
                refined.append(sub_chunk)

    return refined if refined else [text]


def translate_long_text(
    text: str,
    source_lang: str = "English",
    target_lang: str = "Italian",
    provider: str = "anthropic",
    model: str = "claude-sonnet-5",
    chunk_size: int = 2500,
) -> str:
    """
    Translate long text with context-aware chunking.
    Each chunk receives the previous translated chunk as context for coherence.
    """
    if not text or len(text) <= chunk_size:
        return translate_text(text, source_lang, target_lang, provider, model)

    chunks = _split_at_paragraph_boundaries(text, chunk_size)

    translated_chunks = []
    previous_translated = ""

    for chunk in chunks:
        translated = translate_text(
            chunk,
            source_lang,
            target_lang,
            provider,
            model,
            previous_context=previous_translated,
        )
        translated_chunks.append(translated)
        previous_translated = translated

    return '\n'.join(translated_chunks)
