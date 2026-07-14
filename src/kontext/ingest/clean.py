"""text cleanup and plausibility scoring for extracted text."""

from __future__ import annotations

import re

_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_WS = re.compile(r"\s+")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_GOOD_CHARS = re.compile(r"[\w \.,;:!\?\(\)'\"\-–—]", re.UNICODE)


def clean_page_text(text: str) -> str:
    """pdf page text: rejoin words hyphenated across line breaks, then
    collapse layout whitespace. paragraph structure inside a page is not
    reliably recoverable from pdfs, so a page is one block."""
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    return _WS.sub(" ", text).strip()


def normalize_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def split_paragraphs(text: str) -> list[str]:
    return [p for p in (normalize_ws(p) for p in _PARAGRAPH_SPLIT.split(text)) if p]


def count_words(text: str) -> int:
    return len(text.split())


def quality_score(sample: str) -> float:
    """share of characters that belong in prose. clean extractions score
    ~0.97+; encoding garbage, binary junk or formula-heavy dumps drop low."""
    if not sample:
        return 0.0
    return len(_GOOD_CHARS.findall(sample)) / len(sample)
