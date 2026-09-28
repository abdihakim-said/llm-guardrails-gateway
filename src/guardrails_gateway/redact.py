"""Redact personal data and secrets before anything is logged.

Pattern-based, with checksums where the identifier has one (NHS number,
payment card) to cut false positives. It will miss free-text personal data
such as names and addresses; see the README's limitations.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_UK_PHONE = re.compile(r"(?<!\d)(?:\+44\s?7\d{3}|07\d{3})\s?\d{3}\s?\d{3}(?!\d)")
# First letter excludes D F I Q U V; second also excludes O (HMRC format rules).
_NI_NUMBER = re.compile(r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.I)
_NHS_CANDIDATE = re.compile(r"(?<!\d)\d{3}[\s-]?\d{3}[\s-]?\d{4}(?!\d)")
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_SECRETS = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,})\b"
)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def is_valid_nhs_number(candidate: str) -> bool:
    d = _digits(candidate)
    if len(d) != 10:
        return False
    total = sum(int(d[i]) * (10 - i) for i in range(9))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    return check != 10 and check == int(d[9])


def passes_luhn(candidate: str) -> bool:
    d = _digits(candidate)
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _sub_if(pattern: re.Pattern, label: str, check: Callable[[str], bool] | None = None):
    def apply(text: str) -> str:
        def repl(m: re.Match) -> str:
            return f"[{label}]" if check is None or check(m.group(0)) else m.group(0)

        return pattern.sub(repl, text)

    return apply


# Order matters: secrets and structured numbers before the looser patterns.
_RULES = [
    _sub_if(_SECRETS, "SECRET"),
    _sub_if(_EMAIL, "EMAIL"),
    _sub_if(_NHS_CANDIDATE, "NHS_NUMBER", is_valid_nhs_number),
    _sub_if(_CARD_CANDIDATE, "CARD", passes_luhn),
    _sub_if(_UK_PHONE, "PHONE"),
    _sub_if(_NI_NUMBER, "NI_NUMBER"),
]


def redact(text: str) -> str:
    for rule in _RULES:
        text = rule(text)
    return text
