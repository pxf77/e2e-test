from __future__ import annotations

import re
from typing import Any

_SECRET_KEYS = re.compile(r"(password|passwd|secret|token|authorization|api[_-]?key|private[_-]?key|cookie)", re.I)
_EMAIL_KEYS = re.compile(r"email", re.I)
_PHONE_KEYS = re.compile(r"(phone|mobile|tel)", re.I)
_ID_KEYS = re.compile(r"(id[_-]?card|identity|ssn|身份证)", re.I)


def mask_data(value: Any, *, force: bool = False, key: str = "") -> Any:
    """Return an artifact-safe copy of test data.

    Runtime data remains available in memory, while persisted artifacts use this
    representation. Secret-like fields are fully redacted; common PII fields are
    partially masked to retain debugging value without exposing raw values.
    """
    if isinstance(value, dict):
        return {str(item_key): mask_data(item_value, force=force, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [mask_data(item, force=force, key=key) for item in value]
    if isinstance(value, tuple):
        return [mask_data(item, force=force, key=key) for item in value]
    if value is None:
        return None
    text = str(value)
    if force or _SECRET_KEYS.search(key):
        return "***REDACTED***"
    if _EMAIL_KEYS.search(key) and "@" in text:
        local, domain = text.split("@", 1)
        prefix = local[:1] or "*"
        return f"{prefix}***@{domain}"
    if _PHONE_KEYS.search(key):
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 7:
            return f"{digits[:3]}****{digits[-4:]}"
    if _ID_KEYS.search(key) and len(text) >= 8:
        return f"{text[:3]}{'*' * max(len(text) - 7, 4)}{text[-4:]}"
    return value
