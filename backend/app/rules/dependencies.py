"""Pinned dependencies (PRD 9.6).

Details hold package NAMES only, never version specs, URLs or raw lines: those can carry
credentials (S7).
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import Any

from app.rules._manifests import Manifest, find_lock_file, find_manifests
from app.rules.base import Rule, RuleResult, Status
from app.scanner.snapshot import RepoSnapshot

MAX_UNPINNED_LISTED = 20
EXACT_NPM_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_SHA_REF_RE = re.compile(r"@[0-9a-fA-F]{40}\b")
_DIRECT_REF_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*@\s*[A-Za-z][A-Za-z0-9+.-]*://")
_EGG_RE = re.compile(r"[#&]egg=([A-Za-z0-9._-]{1,100})")
_COMMENT_RE = re.compile(r"(^|\s)#.*$")
_IGNORED_REQ_PREFIXES = ("-r", "-c", "-e", "-i", "-f", "--")
_GENERIC_URL_NAME = "url-requirement"
_VERDICT_ORDER = {"pass": 0, "warn": 1, "fail": 2}
_NOT_EXACT_CHARS = "^~*><,"
LOCK_NOTE = "No lock file: fine for a library, required for an application"

FIX_TIP = (
    "Commit a lock file (`uv lock`, `poetry lock`, `npm install` → `package-lock.json`), or "
    "pin exact versions (`pip-compile`, `pip freeze`)."
)


def normalize_python_name(name: str) -> str:
    """PEP 503 normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    """A safe display name; URLs never leak (only a validated #egg= name or a generic label)."""
    if "://" in requirement:
        direct = _DIRECT_REF_RE.match(requirement)  # PEP 508: "name @ url"
        if direct:
            return normalize_python_name(direct.group(1))
        egg = _EGG_RE.search(requirement)
        return normalize_python_name(egg.group(1)) if egg else _GENERIC_URL_NAME
    match = _NAME_RE.match(requirement)
    return normalize_python_name(match.group(1)) if match else _GENERIC_URL_NAME


def parse_requirements(text: str) -> list[tuple[str, bool]]:
    """Return (name, pinned) for each requirement line of a requirements*.txt file."""
    logical: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        logical.append(buffer + line)
        buffer = ""
    if buffer:
        logical.append(buffer)

    result: list[tuple[str, bool]] = []
    for raw in logical:
        line = _COMMENT_RE.sub("", raw).split(";", 1)[0].strip()
        if not line or line.startswith(_IGNORED_REQ_PREFIXES):
            continue
        pinned = "==" in line or bool(_SHA_REF_RE.search(line))
        result.append((_requirement_name(line), pinned))
    return result


def _pep508_pinned(spec: str) -> bool:
    s = spec.split(";", 1)[0].strip()
    if "://" in s:  # direct reference
        return bool(_SHA_REF_RE.search(s))
    match = _NAME_RE.match(s)
    if not match:
        return False
    rest = re.sub(r"\[[^\]]*\]", "", s[match.end() :]).strip().strip("()")
    clauses = [c.strip() for c in rest.split(",") if c.strip()]
    return any(c.startswith("==") and "*" not in c for c in clauses)


def _poetry_exact(value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("version")
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(ch in value for ch in _NOT_EXACT_CHARS)
        and "://" not in value
    )


def _python_deps(data: dict[str, Any]) -> list[tuple[str, bool]]:
    project = data.get("project")
    if isinstance(project, dict) and isinstance(project.get("dependencies"), list):
        return [
            (_requirement_name(s), _pep508_pinned(s))
            for s in project["dependencies"]
            if isinstance(s, str)
        ]
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    deps = poetry.get("dependencies") if isinstance(poetry, dict) else None
    if isinstance(deps, dict):
        return [
            (normalize_python_name(str(name)), _poetry_exact(value))
            for name, value in deps.items()
            if name.lower() != "python"
        ]
    return []


def _npm_deps(data: Any) -> list[tuple[str, bool]]:
    deps: list[tuple[str, bool]] = []
    if not isinstance(data, dict):
        return deps
    for section in ("dependencies", "devDependencies"):
        block = data.get(section)
        if isinstance(block, dict):
            for name, version in block.items():
                exact = isinstance(version, str) and bool(EXACT_NPM_RE.match(version))
                deps.append((str(name)[:100], exact))
    return deps


def _cargo_dep_count(data: dict[str, Any]) -> int:
    count = 0
    for table in ("dependencies", "dev-dependencies", "build-dependencies"):
        block = data.get(table)
        if isinstance(block, dict):
            count += len(block)
    workspace = data.get("workspace")
    block = workspace.get("dependencies") if isinstance(workspace, dict) else None
    return count + (len(block) if isinstance(block, dict) else 0)


def _unpinned_names(deps: list[tuple[str, bool]]) -> list[str]:
    return sorted({name for name, pinned in deps if not pinned})[:MAX_UNPINNED_LISTED]


def _record(
    manifest: Manifest,
    ecosystem: str,
    lock: str | None,
    verdict: str,
    total: int | None,
    unpinned: list[str],
    parse_error: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": manifest.entry.path,
        "ecosystem": ecosystem,
        "lock_file": lock,
        "verdict": verdict,
        "total": total,
        "unpinned": unpinned,
    }
    if parse_error:
        entry["parse_error"] = True
    return entry


def _evaluate(snap: RepoSnapshot, manifest: Manifest) -> dict[str, Any] | None:
    """Verdict record for one manifest, or None if it should be ignored."""
    kind = manifest.kind
    lock = find_lock_file(snap, manifest)
    text = snap.read_text(manifest.entry.path)
    ecosystem = {
        "requirements": "python", "pyproject": "python", "pipfile": "python",
        "package_json": "node", "cargo": "rust", "go": "go", "gemfile": "ruby",
    }[kind]  # fmt: skip

    def parse_error() -> dict[str, Any]:
        return _record(manifest, ecosystem, lock, "warn", None, [], parse_error=True)

    if kind == "go":
        if text is not None and not re.search(r"(?m)^\s*require\b", text):
            return None
        return _record(manifest, ecosystem, lock, "pass" if lock else "fail", None, [])
    if kind in ("gemfile", "pipfile"):
        return _record(manifest, ecosystem, lock, "pass" if lock else "fail", None, [])

    if text is None:
        return parse_error()

    if kind == "requirements":
        deps = parse_requirements(text)
        if not deps:
            return None
        bad = [d for d in deps if not d[1]]
        if not bad:
            verdict = "pass"
        elif len(bad) * 2 <= len(deps):
            verdict = "warn"
        else:
            verdict = "fail"
        return _record(manifest, ecosystem, None, verdict, len(deps), _unpinned_names(deps))

    if kind == "cargo":
        try:
            count = _cargo_dep_count(tomllib.loads(text))
        except tomllib.TOMLDecodeError:
            return parse_error()
        if count == 0:
            return None
        return _record(manifest, ecosystem, lock, "pass" if lock else "warn", count, [])

    try:
        if kind == "pyproject":
            deps = _python_deps(tomllib.loads(text))
        else:  # package_json
            deps = _npm_deps(json.loads(text))
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, RecursionError):
        return parse_error()
    if not deps:
        return None
    if lock:
        return _record(manifest, ecosystem, lock, "pass", len(deps), [])
    unpinned = _unpinned_names(deps)
    all_exact = all(pinned for _, pinned in deps)
    return _record(manifest, ecosystem, None, "pass" if all_exact else "warn", len(deps), unpinned)


class DependenciesRule(Rule):
    id = "dependencies"
    name = "Pinned dependencies"
    description = "Dependencies are pinned or locked so builds are reproducible."
    weight = 10
    display_order = 60
    fix_tip = FIX_TIP

    def check(self, snap: RepoSnapshot) -> RuleResult:
        records = [r for m in find_manifests(snap) if (r := _evaluate(snap, m)) is not None]
        if not records:
            return RuleResult(
                Status.NOT_APPLICABLE, "No dependency manifests found.", {"manifests": []}
            )
        details = {"manifests": records}
        worst = max((r["verdict"] for r in records), key=lambda v: _VERDICT_ORDER[v])
        bad = sum(1 for r in records if r["verdict"] != "pass")
        if worst == "pass":
            return RuleResult(
                Status.PASS, "All dependency manifests are pinned or have a lock file.", details
            )
        message = f"{bad} of {len(records)} dependency manifest(s) are not fully pinned or locked."
        if worst == "warn":
            return RuleResult(Status.WARN, message + f" ({LOCK_NOTE}.)", details)
        return RuleResult(Status.FAIL, message, details)


rule = DependenciesRule()
