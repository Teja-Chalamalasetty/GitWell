# How to add a rule

A rule is **one source file** in `backend/app/rules/` plus **one test file** in
`backend/tests/rules/`. Nothing else changes: rules are discovered automatically at startup, the API
returns them in order, and the UI renders whatever the API returns.

## The contract

A rule is a subclass of `Rule` and the module exports one instance named `rule`:

| Attribute | Meaning |
|---|---|
| `id` | Stable `snake_case` identifier. Stored in the database. **Never rename it.** |
| `name` | Display name, shown in the UI and in issue drafts. |
| `description` | One sentence on what the rule checks. |
| `weight` | A positive integer. A rule's share of the score is `weight / sum of all weights`. |
| `display_order` | Unique integer. Order in the UI, the API and the issue body (lowest first). |
| `fix_tip` | Markdown telling the reader exactly how to fix a failure. |
| `check(snap)` | Returns a `RuleResult`. |

`RuleResult` has a `status`, a short `message` (one or two sentences, specific to this repo) and an
optional `details` dict.

| Status | Counts toward the score as |
|---|---|
| `PASS` | 1.0 |
| `WARN` | 0.5 |
| `FAIL` | 0.0 |
| `NOT_APPLICABLE` | excluded (use it when the rule does not apply, such as "no dependency manifest") |
| `ERROR` | excluded; set by the engine if `check` raises. Do not return it yourself |

A duplicate `id` or `display_order` is a startup error, so pick an unused `display_order`
(`docs/DECISIONS.md` and the existing rules show which are taken).

## What a rule may and may not do

A rule receives a **snapshot** and nothing else. It must be a pure function of that snapshot.

- Do **not** call GitHub, the database, the network or the filesystem.
- Do **not** read the clock or random numbers. The same commit and the same rules must always give
  the same result, on any operating system.
- Do **not** put secret values anywhere. Not in `message`, not in `details`, not in logs. Record
  locations and names, never contents. Anything in `details` is stored and returned by the API.
- `details` must be JSON-serialisable.
- Messages and details may end up in a **public** GitHub issue. Keep repo-derived text out of
  `message`; if you must quote a path or a package name, wrap it with `safe_inline()` from
  `app/services/issue_body.py` (backticks removed, `@` defused).
- If you loop over many files, call `snap.check_deadline()` every few hundred iterations so a huge
  repository cannot run past the scan time limit.

If `check` raises, the engine records an `error` result with a fixed message and the scan carries
on. The exception text is never stored (it could contain repository content).

## The snapshot

```python
snap.full_name, snap.default_branch, snap.commit_sha
snap.mode              # "archive" (everything loaded) or "api" (large repo: partial)
snap.is_partial, snap.partial_reasons

snap.files(include_ignored=False)  # list[FileEntry(path, size)], sorted by path
snap.dirs()                        # set of directory paths
snap.root_file(["README.md", "README"])   # case-insensitive, root only, first match by order
snap.glob("**/*.yml")              # case-insensitive; "*" and "?" never cross "/"
snap.read_text(path)               # str, or None (not loaded, too large, binary, not UTF-8)
```

`files()` leaves out directories named `node_modules`, `vendor`, `.venv`, `dist`, `build` and a few
others (`IGNORED_DIRS`) unless you pass `include_ignored=True`. Paths are POSIX, relative to the
repository root. Reading a file that is not loaded returns `None`: treat a non-empty file whose text
you cannot read as **non-empty** (`is_empty()` in `app/rules/_util.py` does this).

In API mode only a small fixed set of files has content (README, license, manifests, workflows,
`.gitignore`, contributing docs). Decide what your rule should say when content is missing; a
`WARN` that says the check was limited is usually right.

## Worked example

This is a complete rule. It is extracted and run by the test suite, so it always works as written.

<!-- example-rule -->
```python
from __future__ import annotations

from app.rules._util import is_empty
from app.rules.base import Rule, RuleResult, Status
from app.scanner.snapshot import RepoSnapshot

NAMES = ["CHANGELOG.md", "CHANGELOG", "HISTORY.md", "CHANGES.md"]


class ChangelogRule(Rule):
    id = "changelog"
    name = "Changelog"
    description = "The repository keeps a changelog."
    weight = 5
    display_order = 90
    fix_tip = "Add a `CHANGELOG.md` listing notable changes per release."

    def check(self, snap: RepoSnapshot) -> RuleResult:
        entry = snap.root_file(NAMES)
        if entry is None:
            return RuleResult(Status.FAIL, "No changelog found in the repository root.")
        if is_empty(snap, entry):
            return RuleResult(Status.WARN, "The changelog is empty.", {"path": entry.path})
        return RuleResult(Status.PASS, "Changelog found.", {"path": entry.path})


rule = ChangelogRule()
```

Save it as `backend/app/rules/changelog.py`. That is the whole change to the application.

## Test it

`FakeSnapshot` builds a snapshot from a dict of `{path: content}` using the real snapshot logic.
Test **every status** your rule can return, plus the edge cases (case-insensitive names, empty
files, unreadable content, nested directories). Save it as `backend/tests/rules/test_changelog.py`:

```python
from app.rules.base import Status
from app.rules.changelog import rule
from tests.fakes import FakeSnapshot, Unreadable


def test_fail_when_missing() -> None:
    assert rule.check(FakeSnapshot({"README.md": "x"})).status is Status.FAIL


def test_pass_for_any_known_name_in_any_case() -> None:
    assert rule.check(FakeSnapshot({"changelog.MD": "## 1.0"})).status is Status.PASS


def test_warn_when_empty() -> None:
    assert rule.check(FakeSnapshot({"CHANGELOG.md": " \n"})).status is Status.WARN


def test_unreadable_but_non_empty_counts_as_present() -> None:
    assert rule.check(FakeSnapshot({"CHANGELOG.md": Unreadable(9000)})).status is Status.PASS
```

Use `ExplodingSnapshot` (also in `tests/fakes.py`) to prove the engine turns a crash into an
`error` result. Run `uv run pytest tests/rules/test_changelog.py`.

## Things that change when you add a rule

- **The score of every repository.** Weights are relative, so adding a rule lowers or raises every
  existing score a little. That is expected.
- **`ruleset_version`.** It is a hash of the rule ids and weights and is stored with each scan. The
  score delta between two scans is shown only when both used the same rule set, so the first
  re-scan after adding a rule shows no delta. Nothing is lost.
- **GitWell's own score.** GitWell scans its own repository in CI and must score 100. A new rule
  has to pass on this repository, or the change must also bring the repository up to standard.
- **The tests that pin the rule set.** `tests/rules/test_registry.py` and
  `tests/scanner/test_scoring.py` list the current rules and weights on purpose; update them in the
  same change and say why in `docs/DECISIONS.md`.
