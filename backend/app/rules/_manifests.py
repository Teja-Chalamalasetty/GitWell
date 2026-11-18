"""Dependency manifest discovery, shared by the `dependencies` and `gitignore` rules (PRD 9.6)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rules._util import basename
from app.scanner.snapshot import FileEntry, RepoSnapshot

MAX_DEPTH = 2  # root, */ and */*/

_REQUIREMENTS_RE = re.compile(r"^requirements.*\.txt$", re.IGNORECASE)

# manifest kind -> lock file names (same directory)
LOCK_FILES: dict[str, tuple[str, ...]] = {
    "requirements": (),
    "pyproject": ("poetry.lock", "uv.lock", "pdm.lock"),
    "package_json": (
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "bun.lock",
    ),
    "cargo": ("Cargo.lock",),
    "go": ("go.sum",),
    "gemfile": ("Gemfile.lock",),
    "pipfile": ("Pipfile.lock",),
}
_FIXED_NAMES = {
    "pyproject.toml": "pyproject",
    "package.json": "package_json",
    "cargo.toml": "cargo",
    "go.mod": "go",
    "gemfile": "gemfile",
    "pipfile": "pipfile",
}


@dataclass(frozen=True)
class Manifest:
    entry: FileEntry
    kind: str
    directory: str


def manifest_kind(path: str) -> str | None:
    name = basename(path).lower()
    if _REQUIREMENTS_RE.match(name):
        return "requirements"
    return _FIXED_NAMES.get(name)


def find_manifests(snap: RepoSnapshot) -> list[Manifest]:
    found: list[Manifest] = []
    for entry in snap.files():
        if entry.path.count("/") > MAX_DEPTH:
            continue
        kind = manifest_kind(entry.path)
        if kind is not None:
            found.append(Manifest(entry, kind, entry.path.rpartition("/")[0]))
    return sorted(found, key=lambda m: m.entry.path)


def find_lock_file(snap: RepoSnapshot, manifest: Manifest) -> str | None:
    """A lock file only counts if it is in the same directory as its manifest."""
    wanted = {name.lower() for name in LOCK_FILES[manifest.kind]}
    if not wanted:
        return None
    matches = sorted(
        e.path
        for e in snap.files()
        if e.path.rpartition("/")[0] == manifest.directory and basename(e.path).lower() in wanted
    )
    return matches[0] if matches else None
