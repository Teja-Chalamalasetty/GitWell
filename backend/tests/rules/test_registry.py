from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from app.rules.base import Rule
from app.rules.registry import RegistryError, discover_rules, get_rule, get_rules
from tests.helpers import V1_RULE_IDS


def test_the_eight_v1_rules_are_discovered_in_display_order() -> None:
    # Adding a rule must not break this: the v1 rules must be present, not alone.
    rules = [r for r in get_rules() if r.id in V1_RULE_IDS]
    assert [(r.display_order, r.id, r.weight) for r in rules] == [
        (10, "secrets", 20),
        (20, "readme", 15),
        (30, "tests", 15),
        (40, "ci", 15),
        (50, "license", 10),
        (60, "dependencies", 10),
        (70, "gitignore", 10),
        (80, "docs", 5),
    ]
    assert sum(r.weight for r in rules) == 100  # the v1 weights


def test_every_rule_has_the_full_contract() -> None:
    for rule in get_rules():
        assert isinstance(rule, Rule)
        for attr in ("id", "name", "description", "fix_tip"):
            assert getattr(rule, attr).strip(), f"{rule.id}.{attr}"
        assert rule.weight > 0 and rule.display_order > 0


def test_get_rule_by_id() -> None:
    rule = get_rule("readme")
    assert rule is not None and rule.name == "README"
    assert get_rule("nope") is None


def _package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, modules: dict[str, str]
) -> str:
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for module, source in modules.items():
        (pkg / f"{module}.py").write_text(textwrap.dedent(source))
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


RULE_TEMPLATE = """
from app.rules.base import Rule, RuleResult, Status

class _R(Rule):
    id = "{id}"
    name = "N"
    description = "D"
    weight = {weight}
    display_order = {order}
    fix_tip = "tip"

    def check(self, snap):
        return RuleResult(Status.PASS, "ok")

rule = _R()
"""


def rule_src(id: str, order: int, weight: int = 5) -> str:
    return RULE_TEMPLATE.format(id=id, order=order, weight=weight)


def test_adding_a_rule_is_one_new_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _package(
        tmp_path, monkeypatch, "plug_one", {"b": rule_src("b_rule", 2), "a": rule_src("a_rule", 1)}
    )
    assert [r.id for r in discover_rules(pkg)] == ["a_rule", "b_rule"]
    (tmp_path / pkg / "c.py").write_text(rule_src("c_rule", 3))
    import importlib

    importlib.invalidate_caches()
    assert [r.id for r in discover_rules(pkg)] == ["a_rule", "b_rule", "c_rule"]


def test_duplicate_id_is_a_startup_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _package(
        tmp_path, monkeypatch, "plug_dup_id", {"a": rule_src("same", 1), "b": rule_src("same", 2)}
    )
    with pytest.raises(RegistryError, match="duplicate rule id 'same'"):
        discover_rules(pkg)


def test_duplicate_display_order_is_a_startup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _package(
        tmp_path,
        monkeypatch,
        "plug_dup_order",
        {"a": rule_src("a_rule", 7), "b": rule_src("b_rule", 7)},
    )
    with pytest.raises(RegistryError, match="duplicate display_order 7"):
        discover_rules(pkg)


@pytest.mark.parametrize("bad_id", ["CamelCase", "has-dash", "1starts_with_digit", ""])
def test_ids_must_be_snake_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_id: str
) -> None:
    pkg = _package(
        tmp_path, monkeypatch, f"plug_id_{abs(hash(bad_id))}", {"a": rule_src(bad_id, 1)}
    )
    with pytest.raises(RegistryError, match="snake_case"):
        discover_rules(pkg)


def test_weight_must_be_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _package(tmp_path, monkeypatch, "plug_weight", {"a": rule_src("a_rule", 1, weight=0)})
    with pytest.raises(RegistryError, match="positive weight"):
        discover_rules(pkg)


def test_modules_without_a_rule_object_and_underscore_modules_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _package(
        tmp_path,
        monkeypatch,
        "plug_ignore",
        {
            "good": rule_src("good", 1),
            "helpers": "VALUE = 1\n",
            "not_a_rule": "rule = 'a string, not a Rule'\n",
            "_private": rule_src("hidden", 2),
        },
    )
    assert [r.id for r in discover_rules(pkg)] == ["good"]


def test_registry_is_cached() -> None:
    assert get_rules() is get_rules()
    assert "app.rules.secrets" in sys.modules
