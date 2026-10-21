"""PII masking on ingest (deck slide 4: 'mask on ingest')."""
import hashlib
import hmac
import os
import re

_SECRET = os.environ.get("MASK_SECRET", "dev-only-change-me").encode()

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b(?:\+?\d{1,3}[ -]?)?(?:\(?\d{3}\)?[ -]?)\d{3}[ -]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "[IP]"),
    (re.compile(r"(?i)\b(bearer|token|apikey|api_key|password)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
]


def tokenise(value):
    """Stable, irreversible token for a customer id (same id -> same token)."""
    if value in (None, ""):
        return "UNKNOWN"
    digest = hmac.new(_SECRET, str(value).encode(), hashlib.sha256).hexdigest()[:10]
    return f"TNT-{digest}"


def mask_text(text):
    if not text:
        return text
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text
