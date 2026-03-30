"""
ULTIMATE TRANSLATOR - AI Translation Engine
Supports both OpenAI and Anthropic providers.
Context-aware chunking for coherent book-length translations.
"""

import os
import re
import time
import logging
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
    "claude-opus-4-20250514": {
        "provider": "anthropic",
        "display_name": "Claude Opus 4",
        "input_cost": 15.00,
        "output_cost": 75.00,
        "quality": "very_high",
        "speed": "slow",
        "cost_indicator": "$$$$",
        "quality_badge": "Miglior Qualita",
        "description": "Il modello piu potente di Anthropic",
    },
    "claude-sonnet-4-20250514": {
        "provider": "anthropic",
        "display_name": "Claude Sonnet 4",
        "input_cost": 3.00,
        "output_cost": 15.00,
        "quality": "high",
        "speed": "fast",
        "cost_indicator": "$$",
        "quality_badge": "Bilanciato",
        "description": "Ottimo bilanciamento qualita/velocita",
    },
    "claude-haiku-3-5-20241022": {
        "provider": "anthropic",
        "display_name": "Claude Haiku 3.5",
        "input_cost": 0.80,
        "output_cost": 4.00,
        "quality": "good",
        "speed": "very_fast",
        "cost_indicator": "$",
        "quality_badge": "Veloce",
        "description": "Velocissimo e economico",
    },
}


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

def detect_language(text_sample: str, provider: str = "anthropic", model: str = "claude-sonnet-4-20250514") -> str:
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
# Core translation function
# ---------------------------------------------------------------------------

def translate_text(
    text: str,
    source_lang: str = "English",
    target_lang: str = "Italian",
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
    previous_context: str = "",
    max_retries: int = 3,
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

    # Models that require max_completion_tokens instead of max_tokens
    NEEDS_MAX_COMPLETION_TOKENS = {"gpt-5.2", "o1", "o3", "o3-mini", "o4-mini"}

    for attempt in range(max_retries):
        try:
            if provider == "openai":
                cl = get_openai_client()
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ]

                kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.3,
                }

                # Some models need max_completion_tokens, others max_tokens
                if model in NEEDS_MAX_COMPLETION_TOKENS:
                    kwargs["max_completion_tokens"] = 8192
                else:
                    kwargs["max_tokens"] = 8192

                response = cl.chat.completions.create(**kwargs)
                translated = response.choices[0].message.content
            else:
                cl = get_anthropic_client()
                response = cl.messages.create(
                    model=model,
                    max_tokens=8192,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    temperature=0.3,
                )
                translated = response.content[0].text

            if translated:
                return translated.strip()
            return text
        except Exception as e:
            wait_time = 2 ** attempt
            logger.warning(
                f"Translation attempt {attempt + 1}/{max_retries} failed: {e}. "
                f"Retrying in {wait_time}s..."
            )
            if attempt < max_retries - 1:
                time.sleep(wait_time)
            else:
                logger.error(f"Translation failed after {max_retries} attempts: {e}")
                raise


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
    model: str = "claude-sonnet-4-20250514",
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
