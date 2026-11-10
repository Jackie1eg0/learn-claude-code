#!/usr/bin/env python3
"""Small, dependency-free string helpers for the s06_subagent example."""

import re
import unicodedata

__all__ = ["slugify"]

# Characters NFKD does not decompose into ASCII equivalents on its own.
_TRANSLIT = str.maketrans({
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
    "þ": "th", "Þ": "Th",
})


def slugify(text: str) -> str:
    """Convert *text* into a lowercase, URL-friendly slug.

    Accents are folded to ASCII, every run of non-alphanumeric characters
    collapses to a single hyphen, and leading/trailing hyphens are stripped.

    >>> slugify("Hello, World!")
    'hello-world'
    >>> slugify("  Caf\u00e9 d\u00e9j\u00e0 vu  ")
    'cafe-deja-vu'
    >>> slugify("Stra\u00dfe des 17. Juni")
    'strasse-des-17-juni'
    >>> slugify("***")
    ''
    """
    if not isinstance(text, str):
        raise TypeError(f"slugify() expected str, got {type(text).__name__}")

    folded = unicodedata.normalize("NFKD", text.translate(_TRANSLIT))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower())
    return slug.strip("-")


if __name__ == "__main__":
    for sample in ("Hello, World!", "Crème Brûlée", "  Straße des 17. Juni  "):
        print(f"slugify({sample!r}) -> {slugify(sample)!r}")
