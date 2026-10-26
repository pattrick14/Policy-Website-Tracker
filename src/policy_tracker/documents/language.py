"""Dependency-free script-ratio language detection (D-005).

Only the scripts matter for the tracker's English/bilingual policy: Devanagari
letters suggest Hindi, Latin letters suggest English. A document that mixes
both substantially is ``"bilingual"``. Legacy-font Hindi PDFs whose text layer
decodes to Latin garbage cannot be recognised this way.
"""

from __future__ import annotations

HINDI = "hi"
ENGLISH = "en"
BILINGUAL = "bilingual"

# A script must supply at least this share of letters to count as substantial.
SUBSTANTIAL_SHARE = 0.2


def _is_devanagari(char: str) -> bool:
    code = ord(char)
    return 0x0900 <= code <= 0x097F or 0xA8E0 <= code <= 0xA8FF


def _is_latin(char: str) -> bool:
    code = ord(char)
    return ("a" <= char <= "z") or ("A" <= char <= "Z") or (0x00C0 <= code <= 0x024F)


def script_counts(text: str) -> tuple[int, int]:
    """Return ``(devanagari, latin)`` character counts."""

    devanagari = latin = 0
    for char in text:
        if _is_devanagari(char):
            devanagari += 1
        elif _is_latin(char) and char.isalpha():
            latin += 1
    return devanagari, latin


def detect_language(text: str) -> str | None:
    """Return ``"hi"``, ``"en"``, ``"bilingual"``, or ``None`` when no letters are present."""

    devanagari, latin = script_counts(text)
    total = devanagari + latin
    if total == 0:
        return None
    share = devanagari / total
    if share >= 1 - SUBSTANTIAL_SHARE:
        return HINDI
    if share <= SUBSTANTIAL_SHARE:
        return ENGLISH
    return BILINGUAL
