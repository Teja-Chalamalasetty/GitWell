from __future__ import annotations

ELLIPSIS = "…"


def truncate(text: str, limit: int) -> str:
    """Truncate to `limit` characters, ending with an ellipsis (PRD 6.1 string limits)."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + ELLIPSIS
