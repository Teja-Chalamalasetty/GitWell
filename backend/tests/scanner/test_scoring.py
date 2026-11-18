import hashlib

import pytest

from app.rules.base import Status
from app.rules.registry import get_rules
from app.scanner.scoring import ScoredRule, compute_score, grade_for, ruleset_version
from tests.helpers import v1_rules


def s(rule_id: str, weight: int, status: Status) -> ScoredRule:
    return ScoredRule(rule_id, weight, status)


def test_all_pass_is_100_all_fail_is_0() -> None:
    assert compute_score([s("a", 10, Status.PASS), s("b", 30, Status.PASS)]).score == 100
    assert compute_score([s("a", 10, Status.FAIL), s("b", 30, Status.FAIL)]).score == 0


def test_warn_counts_half() -> None:
    result = compute_score([s("a", 10, Status.WARN), s("b", 10, Status.PASS)])
    assert result.score == 75 and result.grade == "B"


def test_weights_matter() -> None:
    result = compute_score([s("a", 90, Status.PASS), s("b", 10, Status.FAIL)])
    assert result.score == 90


def test_not_applicable_is_excluded_from_numerator_and_denominator() -> None:
    result = compute_score(
        [s("a", 50, Status.PASS), s("b", 50, Status.NOT_APPLICABLE), s("c", 50, Status.FAIL)]
    )
    assert result.score == 50
    assert result.rules_errored == 0


def test_error_is_excluded_and_counted() -> None:
    result = compute_score(
        [s("secrets", 20, Status.PASS), s("readme", 15, Status.ERROR), s("x", 15, Status.FAIL)]
    )
    assert result.rules_errored == 1
    assert result.score == round(100 * 20 / 35)  # 57


def test_score_is_null_when_nothing_counted() -> None:
    assert compute_score([]).score is None
    only_excluded = compute_score([s("a", 5, Status.NOT_APPLICABLE), s("b", 5, Status.ERROR)])
    assert only_excluded.score is None and only_excluded.grade is None
    assert only_excluded.rules_errored == 1


def test_score_is_null_when_secrets_errored_even_if_everything_else_passes() -> None:
    result = compute_score([s("secrets", 20, Status.ERROR), s("readme", 15, Status.PASS)])
    assert result.score is None and result.grade is None
    assert result.rules_errored == 1


def test_rounding_is_half_to_even_like_python_round() -> None:
    # 12.5 -> 12, 13.5 -> 14 (banker's rounding), computed exactly
    assert compute_score(
        [s("a", 8, Status.PASS), s("b", 56, Status.FAIL), s("c", 0 + 1, Status.WARN)]
    ).score == round(100 * (8 + 0.5) / 65)
    assert compute_score([s("a", 1, Status.PASS), s("b", 7, Status.FAIL)]).score == 12  # 12.5
    assert compute_score([s("a", 27, Status.PASS), s("b", 173, Status.FAIL)]).score == 14  # 13.5


@pytest.mark.parametrize(
    ("score", "grade"),
    [
        (100, "A"),
        (90, "A"),
        (89, "B"),
        (75, "B"),
        (74, "C"),
        (60, "C"),
        (59, "D"),
        (40, "D"),
        (39, "F"),
        (0, "F"),
    ],
)
def test_grade_boundaries(score: int, grade: str) -> None:
    assert grade_for(score) == grade


def test_ruleset_version_is_stable_and_matches_spec_formula() -> None:
    rules = get_rules()
    expected = hashlib.sha256(
        ",".join(sorted(f"{r.id}:{r.weight}" for r in rules)).encode()
    ).hexdigest()[:12]
    assert ruleset_version(rules) == expected
    assert ruleset_version(reversed(list(rules))) == expected  # order independent  # type: ignore[arg-type]
    assert len(expected) == 12
    # pinned literal over the v1 rules: fails loudly if a shipped rule's id or weight changes
    assert ruleset_version(v1_rules()) == "7db779298732"


def test_ruleset_version_changes_with_weights() -> None:
    rules = list(get_rules())

    class Heavier(type(rules[0])):  # type: ignore[misc]
        weight = rules[0].weight + 1

    assert ruleset_version([Heavier(), *rules[1:]]) != ruleset_version(rules)
