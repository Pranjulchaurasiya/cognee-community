"""Pre-ingestion credential and token scrubbing for Read.ai meeting transcripts."""

import re

REDACTION_PATTERNS = [
    (re.compile(r"\bghp_[a-zA-Z0-9]{36}\b"), "[REDACTED_GITHUB_PAT]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\bBearer\s+[a-zA-Z0-9_\-\.]{16,}\b"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)\b(?:api[ _-]?key|token|secret)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{16,}['\"]?\b"
        ),
        "[REDACTED_KEY]",
    ),
    (re.compile(r"(?i)\bpassword\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?\b"), "password=[REDACTED]"),
]


def redact_secrets(text: str | None) -> str:
    """Scrub sensitive credentials from meeting notes, summaries, and transcripts."""
    if not text:
        return ""
    result = text
    for pattern, replacement in REDACTION_PATTERNS:
        result = pattern.sub(replacement, result)
    return result
