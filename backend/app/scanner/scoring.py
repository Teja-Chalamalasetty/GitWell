"""Deterministic scoring (PRD 8.4)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from app.rules.base import Rule, Status

# In halves, so the arithmetic is exact: pass = 1.0, warn = 0.5, fail = 0.0.
_HALVES = {Status.PASS: 2, Status.WARN: 1, Status.FAIL: 0}
SECRETS_RULE_ID = "secrets"


@dataclass(frozen=True)
class ScoredRule:
    rule_id: str
    weight: int
    status: Status


@dataclass(frozen=True)
class ScoreResult:
    score: int | None
    grade: str | None
    rules_errored: int


def grade_for(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def compute_score(results: Sequence[ScoredRule]) -> ScoreResult:
    """`not_applicable` and `error` are excluded from numerator and denominator.

    The score is null if nothing counted, or if the `secrets` rule errored: a crash in the
    highest-weight safety rule must not inflate the score.
    """
    errored = [r for r in results if r.status is Status.ERROR]
    counted = [r for r in results if r.status in _HALVES]
    secrets_errored = any(r.rule_id == SECRETS_RULE_ID for r in errored)
    total_weight = sum(r.weight for r in counted)
    if not counted or total_weight == 0 or secrets_errored:
        return ScoreResult(None, None, len(errored))
    halves = sum(r.weight * _HALVES[r.status] for r in counted)
    # Fraction rounds half-to-even exactly like Python's round(), with no float noise.
    score = round(Fraction(100 * halves, 2 * total_weight))
    return ScoreResult(score, grade_for(score), len(errored))


def ruleset_version(rules: Sequence[Rule]) -> str:
    """First 12 hex chars of sha256 over the sorted `id:weight` pairs."""
    joined = ",".join(sorted(f"{r.id}:{r.weight}" for r in rules))
    return hashlib.sha256(joined.encode()).hexdigest()[:12]
