from __future__ import annotations

from app.rules._manifests import find_manifests
from app.rules._util import basename
from app.rules.base import Rule, RuleResult, Status
from app.scanner.snapshot import RepoSnapshot

ENV_EXCLUDES = frozenset(
    {".env", ".env*", ".env.*", "*.env", "/.env", "**/.env", "/.env*", "**/.env*"}
)


def _excludes_env(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        if line in ENV_EXCLUDES:
            return True
    return False


def _uses_dotenv(snap: RepoSnapshot) -> bool:
    if any(basename(e.path).lower().startswith(".env") for e in snap.files()):
        return True
    for manifest in find_manifests(snap):
        text = snap.read_text(manifest.entry.path)
        if text is not None and "dotenv" in text.lower():
            return True
    return False


class GitignoreRule(Rule):
    id = "gitignore"
    name = ".gitignore"
    description = "A .gitignore exists and keeps .env files out of git."
    weight = 10
    display_order = 70
    fix_tip = (
        "Start from a template at github.com/github/gitignore and add `.env` and `.env.*`, "
        "plus `!.env.example`."
    )

    def check(self, snap: RepoSnapshot) -> RuleResult:
        uses_dotenv = _uses_dotenv(snap)
        entry = snap.root_file([".gitignore"])
        if entry is None:
            return RuleResult(
                Status.FAIL,
                "No .gitignore found in the repository root.",
                {"excludes_env": False, "uses_dotenv": uses_dotenv},
            )
        text = snap.read_text(entry.path)
        # An unreadable .gitignore (e.g. huge) cannot be checked; give it the benefit of the doubt.
        excludes_env = True if text is None else _excludes_env(text)
        details = {"excludes_env": excludes_env, "uses_dotenv": uses_dotenv}
        if uses_dotenv and not excludes_env:
            return RuleResult(Status.WARN, "`.gitignore` does not exclude `.env` files.", details)
        return RuleResult(Status.PASS, ".gitignore found.", details)


rule = GitignoreRule()
