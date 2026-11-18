"""Rule plugin contract (PRD 8.3).

Adding a rule = one new file in this package (exporting a top-level ``rule`` instance) plus its
test file. Rules see only a RepoSnapshot: no GitHub, no database, no filesystem.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from app.scanner.snapshot import RepoSnapshot


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"  # the rule does not apply (e.g. no dependency manifest)
    ERROR = "error"  # the rule crashed; recorded, and the scan continues


@dataclass
class RuleResult:
    status: Status
    message: str  # 1-2 sentences, specific to this repo
    details: dict[str, Any] = field(default_factory=dict)  # JSON-serialisable; NEVER secrets


class Rule(ABC):
    id: ClassVar[str]  # stable snake_case; never renamed
    name: ClassVar[str]
    description: ClassVar[str]
    weight: ClassVar[int]
    display_order: ClassVar[int]  # UI, API and issue body order
    fix_tip: ClassVar[str]  # markdown

    @abstractmethod
    def check(self, snap: RepoSnapshot) -> RuleResult: ...
