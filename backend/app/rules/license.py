from __future__ import annotations

from pathlib import PurePosixPath

from app.rules._util import is_empty, root_files
from app.rules.base import Rule, RuleResult, Status
from app.scanner.snapshot import RepoSnapshot

STEMS = {"license", "licence", "copying", "unlicense"}


class LicenseRule(Rule):
    id = "license"
    name = "License"
    description = "The repository declares a license."
    weight = 10
    display_order = 50
    fix_tip = (
        "Add a `LICENSE` file (see choosealicense.com). On GitHub: Add file → Create new file → "
        'name it `LICENSE` → "Choose a license template".'
    )

    def check(self, snap: RepoSnapshot) -> RuleResult:
        candidates = sorted(
            (e for e in root_files(snap) if PurePosixPath(e.path).stem.lower() in STEMS),
            key=lambda e: e.path,
        )
        if not candidates:
            return RuleResult(Status.FAIL, "No LICENSE file found in the repository root.")
        for entry in candidates:
            if not is_empty(snap, entry):
                return RuleResult(Status.PASS, "License file found.", {"path": entry.path})
        return RuleResult(
            Status.WARN, "A license file exists but it is empty.", {"path": candidates[0].path}
        )


rule = LicenseRule()
