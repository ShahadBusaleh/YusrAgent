"""Arabic <-> English translation at the edge of the agent pipeline.

The Orchestrator uses this to translate an Arabic prompt to English before
it ever reaches intent classification / HR / Consultant / Manager, and to
translate the final English response back to Arabic before it reaches the
user. Every agent downstream keeps operating on English text only — no
Arabic ever touches the database, RAG index, or governance checks.
"""

from __future__ import annotations

import re

from openai import OpenAI

from app.config import get_settings

_ARABIC_CHAR_RE = re.compile(r"[؀-ۿݐ-ݿ]")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def detect_language(text: str) -> str:
    """Return "ar" if `text` is predominantly Arabic script, else "en"."""

    text = text or ""

    letters = _LETTER_RE.findall(text)

    if not letters:
        return "en"

    arabic_letters = _ARABIC_CHAR_RE.findall(text)

    return "ar" if len(arabic_letters) / len(letters) > 0.3 else "en"


def _client() -> tuple[OpenAI, str]:
    settings = get_settings()

    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is not configured in the local .env file.")

    return (
        OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url),
        settings.llm_model,
    )


def _translate(text: str, system_prompt: str) -> str:
    text = (text or "").strip()

    if not text:
        return text

    client, model = _client()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0,
    )

    translated = (response.choices[0].message.content or "").strip()

    return translated or text


def translate_to_english(text: str) -> str:
    """Translate an employee's prompt from Arabic to English.

    Used before the query reaches intent classification, HR/Consultant
    retrieval, and governance checks, so those keep working on English
    text exactly as before.
    """

    return _translate(
        text,
        "Translate the user's message into English. This is an HR "
        "assistant request. Preserve names, dates (keep YYYY-MM-DD "
        "format if present), numbers, IBANs, phone numbers, and email "
        "addresses exactly as written. Return ONLY the translated text, "
        "no notes, no quotes, no explanation.",
    )


def translate_to_arabic(text: str) -> str:
    """Translate the agent's final English response into Arabic.

    Used only on the outgoing `response` string, after the existing
    English-only pipeline (HR/Consultant/Manager) has already produced
    and validated it.
    """

    return _translate(
        text,
        "Translate the following HR assistant response into Modern "
        "Standard Arabic. Preserve [Source: ID] citation tags, Law IDs, "
        "Article numbers, dates, proposal/approval IDs, and bracketed "
        "PII placeholders such as [IBAN], [EMAIL], [PHONE], "
        "[NATIONAL_ID] exactly as written, untranslated. Return ONLY "
        "the translated text, no notes, no quotes, no explanation.",
    )
