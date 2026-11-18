"""Strict repository URL validation (PRD 12.1)."""

from __future__ import annotations

import re

REPO_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)"
    r"/(?P<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
_BAD_NAMES = {".", "..", ".git"}


class InvalidRepoUrl(ValueError):
    """The text is not an acceptable github.com repository URL."""


def parse_repo_url(url: str) -> tuple[str, str]:
    """Return (owner, name) or raise InvalidRepoUrl."""
    match = REPO_URL_RE.fullmatch(url.strip())
    if match is None:
        raise InvalidRepoUrl("Enter a URL like https://github.com/owner/repo.")
    owner, name = match.group("owner"), match.group("name")
    if "--" in owner or name in _BAD_NAMES:
        raise InvalidRepoUrl("Enter a URL like https://github.com/owner/repo.")
    return owner, name
