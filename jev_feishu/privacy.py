from __future__ import annotations
import re
import unicodedata

_PATTERNS = [
    (re.compile(r"\b(?:sk|rk|ghp|github_pat)[-_][A-Za-z0-9_-]{12,}\b"), "[密钥已隐藏]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}"), "Bearer [已隐藏]"),
    (re.compile(r"(?i)(?:api[_ -]?key|app[_ -]?secret|access[_ -]?token|password)\s*[:=]\s*[^\s,;，；]{5,}"), "[凭据已隐藏]"),
    (re.compile(r"(?<!\w)[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)"), "[邮箱已隐藏]"),
    (re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), "[手机号已隐藏]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\w)"), "[证件号已隐藏]"),
]

def clean_text(text: str, *, redact: bool = True) -> str:
    # Keep newlines/tabs, remove bidi and control characters.
    value = "".join(c for c in str(text) if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf"))
    if redact:
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
    return value

def outbound_text(text: str) -> str:
    """Plain text only; disarm mention tags and never render external media."""
    value = clean_text(text, redact=True)
    value = re.sub(r"(?i)<\s*/?\s*at\b[^>]*>", "", value)
    return value.replace("@", "@\u200b")
