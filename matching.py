"""Keyword matching utilities.

Whole-word, case-insensitive matching so that short keywords like "SOS"
or "OK" do not trigger on substrings (e.g. "sossisson", "broken", "book").
"""
import re
from functools import lru_cache


@lru_cache(maxsize=128)
def _pattern_for(keyword: str) -> re.Pattern:
    # \b gives us word boundaries so "OK" won't match inside "broken".
    # re.escape keeps this safe for keywords containing regex metacharacters.
    return re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)


def contains_keyword(text: str, keyword: str) -> bool:
    """Return True if `keyword` appears in `text` as a whole word (case-insensitive).

    An empty keyword never matches — this avoids a misconfigured empty keyword
    silently matching at every word boundary.
    """
    if not keyword:
        return False
    return _pattern_for(keyword).search(text) is not None


def first_matching_keyword(text: str, keywords: list[str]) -> str | None:
    """Return the first keyword that appears in `text` as a whole word, or None."""
    for kw in keywords:
        if kw and contains_keyword(text, kw):
            return kw
    return None