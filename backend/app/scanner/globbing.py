"""Own glob-to-regex translation (PRD 8.2). Not fnmatch, which behaves differently per OS.

Semantics, identical on every OS:
- ``*`` and ``?`` never cross ``/``
- ``**/`` matches zero or more directories; a trailing ``/**`` matches anything below
- ``[abc]`` and ``[!abc]`` character classes
- matching is case-insensitive unless ``case_sensitive=True``
"""

from __future__ import annotations

import re
from functools import lru_cache


def translate(pattern: str) -> str:
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        at_segment_start = i == 0 or pattern[i - 1] == "/"
        if ch == "*":
            if pattern.startswith("**", i) and at_segment_start:
                if pattern.startswith("**/", i):
                    out.append("(?:[^/]+/)*")
                    i += 3
                    continue
                if i + 2 == n:
                    out.append(".*")
                    i += 2
                    continue
            # a run of stars that is not a whole segment behaves like a single "*"
            while i < n and pattern[i] == "*":
                i += 1
            out.append("[^/]*")
            continue
        if ch == "?":
            out.append("[^/]")
        elif ch == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(ch))
            else:
                body = pattern[i + 1 : j]
                negate = body[:1] in ("!", "^")
                if negate:
                    body = body[1:]
                body = body.replace("\\", "\\\\")
                out.append("[" + ("^" if negate else "") + body + "]")
                i = j
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


@lru_cache(maxsize=512)
def compile_glob(pattern: str, *, case_sensitive: bool = False) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(translate(pattern), flags)


def glob_match(pattern: str, path: str, *, case_sensitive: bool = False) -> bool:
    return compile_glob(pattern, case_sensitive=case_sensitive).fullmatch(path) is not None
