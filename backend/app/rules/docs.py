from __future__ import annotations

from app.rules.base import Rule, RuleResult, Status
from app.scanner.snapshot import RepoSnapshot

CONTRIBUTING_PATTERNS = tuple(
    f"{prefix}{name}"
    for prefix in ("", ".github/", "docs/")
    for name in ("CONTRIBUTING", "CONTRIBUTING.md", "CONTRIBUTING.rst")
)


class DocsRule(Rule):
    id = "docs"
    name = "Contributor docs"
    description = "The repository documents how to contribute."
    weight = 5
    display_order = 80
    fix_tip = (
        "Add `CONTRIBUTING.md` covering dev setup, branching, the PR process and code style, "
        "and/or a `docs/` folder."
    )

    def check(self, snap: RepoSnapshot) -> RuleResult:
        contributing = sorted(
            {e.path for pattern in CONTRIBUTING_PATTERNS for e in snap.glob(pattern)}
        )
        has_docs_dir = any(e.path.lower().startswith("docs/") for e in snap.files())
        details = {
            "contributing": contributing[0] if contributing else None,
            "has_docs_dir": has_docs_dir,
        }
        if contributing:
            return RuleResult(Status.PASS, "Contributor guide found.", details)
        if has_docs_dir:
            return RuleResult(Status.PASS, "A docs/ directory is present.", details)
        return RuleResult(Status.FAIL, "No contributor guide or docs/ directory found.", details)


rule = DocsRule()
