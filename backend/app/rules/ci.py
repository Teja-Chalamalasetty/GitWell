from __future__ import annotations

from app.rules._util import is_empty
from app.rules.base import Rule, RuleResult, Status
from app.scanner.snapshot import FileEntry, RepoSnapshot

# (provider id, glob patterns). GitHub Actions: direct children of .github/workflows only.
PROVIDERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("github_actions", (".github/workflows/*.yml", ".github/workflows/*.yaml")),
    ("gitlab_ci", (".gitlab-ci.yml",)),
    ("circleci", (".circleci/config.yml",)),
    ("jenkins", ("Jenkinsfile",)),
    ("azure_pipelines", ("azure-pipelines.yml",)),
    ("travis_ci", (".travis.yml",)),
    ("bitbucket_pipelines", ("bitbucket-pipelines.yml",)),
)

FIX_TIP = """\
Add `.github/workflows/ci.yml` that installs dependencies, lints and runs the tests on every push
and pull request. A minimal example:

```yaml
name: CI
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: pytest
```"""


class CiRule(Rule):
    id = "ci"
    name = "Continuous integration"
    description = "The repository runs automated checks in CI."
    weight = 15
    display_order = 40
    fix_tip = FIX_TIP

    def check(self, snap: RepoSnapshot) -> RuleResult:
        providers: list[str] = []
        files: list[FileEntry] = []
        for provider, patterns in PROVIDERS:
            matched = {e.path: e for pattern in patterns for e in snap.glob(pattern)}
            if matched:
                providers.append(provider)
                files.extend(matched.values())
        files.sort(key=lambda e: e.path)
        details = {"providers": providers, "files": [e.path for e in files]}
        if not files:
            return RuleResult(Status.FAIL, "No CI configuration found.", details)
        if all(is_empty(snap, e) for e in files):
            return RuleResult(Status.WARN, "CI configuration files exist but are empty.", details)
        return RuleResult(Status.PASS, "CI configuration found.", details)


rule = CiRule()
