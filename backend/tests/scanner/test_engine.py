from __future__ import annotations

import logging
import random
from typing import Any

import pytest

from app.rules.base import Rule, RuleResult, Status
from app.rules.registry import get_rules
from app.scanner.deadline import Deadline, ScanTimeout
from app.scanner.engine import RULE_ERROR_MESSAGE, run_rules
from app.scanner.scoring import compute_score
from tests.fakes import ExplodingSnapshot, FakeSnapshot
from tests.helpers import v1_rules

AWS = "AKIA" + "Q" * 16

GOOD_REPO = {
    "README.md": "# Project\n\n" + "This project does many useful things. " * 10,
    "LICENSE": "MIT License",
    "CONTRIBUTING.md": "Please contribute.",
    ".gitignore": ".env\n.env.*\n",
    ".env.example": "A=1",
    ".github/workflows/ci.yml": "name: CI\non: push\n",
    "tests/test_app.py": "def test_x(): pass",
    "src/app.py": "print('hi')",
    "pyproject.toml": '[project]\nname = "x"\ndependencies = ["flask==3.0.0"]\n',
}


def scan(files: dict[str, str]):  # type: ignore[no-untyped-def]
    outcomes = run_rules(FakeSnapshot(files), v1_rules(), Deadline(60))
    return outcomes, compute_score([o.scored() for o in outcomes])


def test_runs_every_rule_in_display_order() -> None:
    outcomes, _ = scan(GOOD_REPO)
    assert [o.rule_id for o in outcomes] == [
        "secrets",
        "readme",
        "tests",
        "ci",
        "license",
        "dependencies",
        "gitignore",
        "docs",
    ]
    assert [o.display_order for o in outcomes] == sorted(o.display_order for o in outcomes)


def test_a_clean_repo_scores_100_and_a_bare_one_scores_low() -> None:
    outcomes, result = scan(GOOD_REPO)
    assert result.score == 100 and result.grade == "A", [
        (o.rule_id, o.status, o.message) for o in outcomes if o.status is not Status.PASS
    ]
    _, bare = scan({"main.py": "x"})
    # secrets passes (20) and dependencies is not applicable: 20/90
    assert bare.score == 22 and bare.grade == "F"


def test_engine_sorts_rules_itself_so_input_order_does_not_matter() -> None:
    rules = list(get_rules())
    random.Random(1).shuffle(rules)
    outcomes = run_rules(FakeSnapshot(GOOD_REPO), rules, Deadline(60))
    assert [o.rule_id for o in outcomes] == [r.id for r in get_rules()]


# ---- determinism ---------------------------------------------------------------------------


def test_scanning_the_same_fixture_twice_gives_identical_results() -> None:
    first, score1 = scan(GOOD_REPO)
    second, score2 = scan(GOOD_REPO)
    assert first == second and score1 == score2


def test_results_do_not_depend_on_file_insertion_order() -> None:
    files = {**GOOD_REPO, "src/leak.py": f"KEY = '{AWS}'", "docs/a.md": "x"}
    baseline, base_score = scan(files)
    items = list(files.items())
    for seed in range(5):
        random.Random(seed).shuffle(items)
        outcomes, score = scan(dict(items))
        assert outcomes == baseline and score == base_score


def test_results_are_serialisable_and_stable_as_json() -> None:
    import json

    outcomes, _ = scan({**GOOD_REPO, "src/leak.py": f"KEY = '{AWS}'"})
    dump = json.dumps(
        [(o.rule_id, o.status.value, o.message, o.details) for o in outcomes], sort_keys=True
    )
    outcomes2, _ = scan({**GOOD_REPO, "src/leak.py": f"KEY = '{AWS}'"})
    assert dump == json.dumps(
        [(o.rule_id, o.status.value, o.message, o.details) for o in outcomes2], sort_keys=True
    )
    assert AWS not in dump


def test_rules_never_see_anything_but_the_snapshot_so_order_cannot_matter() -> None:
    forward = run_rules(FakeSnapshot(GOOD_REPO), get_rules(), Deadline(60))
    backward = run_rules(FakeSnapshot(GOOD_REPO), list(reversed(get_rules())), Deadline(60))
    assert forward == backward


# ---- error isolation -----------------------------------------------------------------------


class Crashing(Rule):
    id = "crashing"
    name = "Crashing"
    description = "always raises"
    weight = 10
    display_order = 35
    fix_tip = "n/a"

    def check(self, snap: Any) -> RuleResult:
        raise ValueError("".join(["secret", "-looking-text "]) + AWS)  # built at runtime


def test_a_raising_rule_becomes_an_error_result_and_the_scan_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    outcomes = run_rules(FakeSnapshot(GOOD_REPO), [*v1_rules(), Crashing()], Deadline(60))
    assert len(outcomes) == 9
    crashed = next(o for o in outcomes if o.rule_id == "crashing")
    assert crashed.status is Status.ERROR
    assert crashed.message == RULE_ERROR_MESSAGE == "Rule failed to run."
    assert crashed.details == {}
    # every other rule still ran normally
    assert all(o.status is Status.PASS for o in outcomes if o.rule_id != "crashing")
    # an errored rule is counted and excluded from the score
    scored = compute_score([o.scored() for o in outcomes])
    assert scored.rules_errored == 1 and scored.score == 100


def test_the_exception_type_and_stack_are_logged_but_not_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    run_rules(FakeSnapshot({}), [Crashing()], Deadline(60))
    record = next(r for r in caplog.records if r.getMessage() == "rule_failed")
    assert record.rule_id == "crashing"  # type: ignore[attr-defined]
    assert record.exc_type == "ValueError"  # type: ignore[attr-defined]
    assert "in check" in record.stack  # type: ignore[attr-defined]
    assert "secret-looking-text" not in record.stack  # type: ignore[attr-defined]
    everything = " ".join(f"{r.getMessage()} {r.__dict__} {r.exc_text}" for r in caplog.records)
    assert "secret-looking-text" not in everything and AWS not in everything


@pytest.mark.parametrize("rule", get_rules(), ids=lambda r: r.id)
def test_every_real_rule_yields_error_on_a_raising_snapshot(rule: Rule) -> None:
    outcomes = run_rules(ExplodingSnapshot({"a.py": "x"}), [rule], Deadline(60))
    assert outcomes[0].status is Status.ERROR
    assert outcomes[0].message == "Rule failed to run."
    assert "boom" not in str(outcomes[0])


def test_all_rules_erroring_gives_a_null_score() -> None:
    outcomes = run_rules(ExplodingSnapshot({"a.py": "x"}), get_rules(), Deadline(60))
    result = compute_score([o.scored() for o in outcomes])
    assert result.score is None and result.rules_errored == len(get_rules())


def test_secrets_erroring_nulls_the_score_even_when_other_rules_pass() -> None:
    class BoomSecrets(Rule):
        id, name, description = "secrets", "S", "d"
        weight, display_order, fix_tip = 20, 10, "t"

        def check(self, snap: Any) -> RuleResult:
            raise RuntimeError

    others = [r for r in v1_rules() if r.id != "secrets"]
    outcomes = run_rules(FakeSnapshot(GOOD_REPO), [BoomSecrets(), *others], Deadline(60))
    result = compute_score([o.scored() for o in outcomes])
    assert result.score is None and result.grade is None and result.rules_errored == 1


# ---- deadline ------------------------------------------------------------------------------


def test_deadline_is_checked_before_each_rule() -> None:
    d = Deadline(60)
    seen: list[str] = []

    class Expiring(Rule):
        id, name, description = "expiring", "E", "d"
        weight, display_order, fix_tip = 1, 1, "t"

        def check(self, snap: Any) -> RuleResult:
            seen.append(self.id)
            d.expire()
            return RuleResult(Status.PASS, "ok")

    class After(Rule):
        id, name, description = "after", "A", "d"
        weight, display_order, fix_tip = 1, 2, "t"

        def check(self, snap: Any) -> RuleResult:
            seen.append(self.id)
            return RuleResult(Status.PASS, "ok")

    with pytest.raises(ScanTimeout):
        run_rules(FakeSnapshot({}), [Expiring(), After()], d)
    assert seen == ["expiring"]


def test_a_timeout_inside_a_rule_is_never_swallowed_as_a_rule_error() -> None:
    class SlowRule(Rule):
        id, name, description = "slow", "S", "d"
        weight, display_order, fix_tip = 1, 1, "t"

        def check(self, snap: Any) -> RuleResult:
            raise ScanTimeout

    with pytest.raises(ScanTimeout):
        run_rules(FakeSnapshot({}), [SlowRule()], Deadline(60))


def test_messages_are_truncated_to_1000_characters() -> None:
    class Wordy(Rule):
        id, name, description = "wordy", "W", "d"
        weight, display_order, fix_tip = 1, 1, "t"

        def check(self, snap: Any) -> RuleResult:
            return RuleResult(Status.PASS, "x" * 5000)

    message = run_rules(FakeSnapshot({}), [Wordy()], Deadline(60))[0].message
    assert len(message) == 1000 and message.endswith("…")
