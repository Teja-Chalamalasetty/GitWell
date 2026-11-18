"""Auto-discovery of rules (PRD 8.3).

Any module in the rules package with a top-level ``rule`` attribute that is a ``Rule`` instance is
registered. A duplicate ``id`` or ``display_order`` is a startup error.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from functools import lru_cache

from app.rules.base import Rule

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SKIP = {"base", "registry"}


class RegistryError(Exception):
    """The rule set is invalid."""


def discover_rules(package_name: str = "app.rules") -> tuple[Rule, ...]:
    package = importlib.import_module(package_name)
    found: list[Rule] = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_") or info.name in _SKIP:
            continue
        module = importlib.import_module(f"{package_name}.{info.name}")
        candidate = getattr(module, "rule", None)
        if isinstance(candidate, Rule):
            found.append(candidate)

    seen_ids: dict[str, Rule] = {}
    seen_orders: dict[int, Rule] = {}
    for rule in found:
        if not _ID_RE.match(rule.id):
            raise RegistryError(f"rule id {rule.id!r} is not snake_case")
        if rule.weight <= 0:
            raise RegistryError(f"rule {rule.id!r} must have a positive weight")
        if rule.id in seen_ids:
            raise RegistryError(f"duplicate rule id {rule.id!r}")
        if rule.display_order in seen_orders:
            raise RegistryError(
                f"duplicate display_order {rule.display_order} "
                f"({seen_orders[rule.display_order].id!r} and {rule.id!r})"
            )
        seen_ids[rule.id] = rule
        seen_orders[rule.display_order] = rule
    return tuple(sorted(found, key=lambda r: r.display_order))


@lru_cache
def get_rules() -> tuple[Rule, ...]:
    return discover_rules()


def get_rule(rule_id: str) -> Rule | None:
    return next((r for r in get_rules() if r.id == rule_id), None)
