from __future__ import annotations

from app.rules._util import is_empty
from app.rules.base import Rule, RuleResult, Status
from app.scanner.snapshot import RepoSnapshot

NAMES = ["README.md", "README", "README.rst", "README.txt", "README.markdown"]
MIN_CHARS = 150


class ReadmeRule(Rule):
    id = "readme"
    name = "README"
    description = "The repository has a README that explains the project."
    weight = 15
    display_order = 20
    fix_tip = (
        "Add a `README.md` covering what the project does, how to install, run and test it, "
        "and how to contribute."
    )

    def check(self, snap: RepoSnapshot) -> RuleResult:
        entry = snap.root_file(NAMES)
        if entry is None:
            return RuleResult(Status.FAIL, "No README found in the repository root.")
        if is_empty(snap, entry):
            return RuleResult(Status.FAIL, "README is empty.", {"path": entry.path, "length": 0})
        text = snap.read_text(entry.path)
        if text is None:
            return RuleResult(
                Status.PASS, "README found.", {"path": entry.path, "length": entry.size}
            )
        length = sum(1 for ch in text if not ch.isspace())
        details = {"path": entry.path, "length": length}
        if length < MIN_CHARS:
            return RuleResult(Status.WARN, f"README is very short ({length} characters).", details)
        return RuleResult(Status.PASS, "README found.", details)


rule = ReadmeRule()
