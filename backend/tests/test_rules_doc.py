"""docs/RULES.md must stay true: its worked example is extracted and run here."""

from __future__ import annotations

import re
import sys
import textwrap
import types
from pathlib import Path

import pytest

from app.rules.base import Rule
from app.rules.registry import discover_rules

DOC = Path(__file__).resolve().parents[2] / "docs" / "RULES.md"


def _blocks() -> list[str]:
    return re.findall(r"```python\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)


def _example_rule_source() -> str:
    match = re.search(
        r"<!-- example-rule -->\n```python\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S
    )
    assert match, "RULES.md lost its <!-- example-rule --> marker"
    return match.group(1)


def _example_test_source() -> str:
    return next(b for b in _blocks() if "def test_fail_when_missing" in b)


@pytest.fixture
def example_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    pkg = tmp_path / "docrules_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "changelog.py").write_text(_example_rule_source())
    monkeypatch.syspath_prepend(str(tmp_path))
    return "docrules_pkg"


def test_the_example_rule_is_discovered_like_any_other(example_package: str) -> None:
    rules = discover_rules(example_package)
    assert [r.id for r in rules] == ["changelog"]
    assert isinstance(rules[0], Rule)
    assert rules[0].weight > 0 and rules[0].fix_tip.strip()


def test_the_examples_own_tests_pass_as_written(example_package: str) -> None:
    source = _example_test_source().replace(
        "from app.rules.changelog import rule", f"from {example_package}.changelog import rule"
    )
    module = types.ModuleType("doc_example_tests")
    sys.modules["doc_example_tests"] = module
    try:
        exec(compile(textwrap.dedent(source), "RULES.md:example-test", "exec"), module.__dict__)
        tests = [fn for name, fn in vars(module).items() if name.startswith("test_")]
        assert len(tests) >= 4
        for fn in tests:
            fn()
    finally:
        sys.modules.pop("doc_example_tests", None)


def test_the_example_does_not_collide_with_a_shipped_rule() -> None:
    from app.rules.registry import get_rules

    namespace: dict[str, object] = {"__name__": "example"}
    exec(compile(_example_rule_source(), "RULES.md:example", "exec"), namespace)
    example = namespace["rule"]
    assert isinstance(example, Rule)
    shipped = get_rules()
    assert example.id not in {r.id for r in shipped}
    assert example.display_order not in {r.display_order for r in shipped}


def test_the_documented_snapshot_api_matches_the_real_one() -> None:
    from app.scanner.snapshot import BaseSnapshot

    text = DOC.read_text(encoding="utf-8")
    for method in ("files", "dirs", "root_file", "glob", "read_text", "check_deadline"):
        assert hasattr(BaseSnapshot, method), method
        assert f"snap.{method}(" in text, f"RULES.md does not document snap.{method}()"
