"""Redaction of secrets and sensitive identifiers.

Used on every log line, audit detail and outbound error message, and by the
memory store to refuse sensitive data. Patterns are conservative: a false
positive costs a masked string, a false negative leaks a secret.
"""
from __future__ import annotations

import re

_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}")),
    ("password_assignment", re.compile(r"(?i)(password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[^\s'\"]{6,}")),
]

_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Payment cards: 13–19 digits with optional separators (Luhn-checked below)
    ("card_number", re.compile(r"\b(?:\d[ \-]?){13,19}\b")),
    # India: Aadhaar (12 digits, first not 0/1), PAN
    ("aadhaar", re.compile(r"\b[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}\b")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("otp_phrase", re.compile(r"(?i)\b(otp|one[- ]time password|cvv|upi pin|atm pin)\b\D{0,12}\d{3,8}")),
]


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for name, pat in _SECRET_PATTERNS:
        out = pat.sub(f"[REDACTED:{name}]", out)
    return out


def find_sensitive(text: str) -> list[str]:
    """Return the kinds of sensitive identifiers/secrets present in text."""
    kinds: list[str] = []
    for name, pat in _SECRET_PATTERNS:
        if pat.search(text):
            kinds.append(name)
    for name, pat in _SENSITIVE_PATTERNS:
        for m in pat.finditer(text):
            if name == "card_number" and not _luhn_ok(m.group(0)):
                continue
            kinds.append(name)
            break
    return sorted(set(kinds))


def redact_sensitive(text: str) -> str:
    out = redact(text)
    for name, pat in _SENSITIVE_PATTERNS:
        def _sub(m: re.Match, _name: str = name) -> str:
            if _name == "card_number" and not _luhn_ok(m.group(0)):
                return m.group(0)
            return f"[REDACTED:{_name}]"
        out = pat.sub(_sub, out)
    return out
