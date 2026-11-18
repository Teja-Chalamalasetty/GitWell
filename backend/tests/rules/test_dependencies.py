import json

import pytest

from app.rules.base import Status
from app.rules.dependencies import normalize_python_name, parse_requirements, rule
from tests.fakes import FakeSnapshot, Unreadable

SHA = "a" * 40


def run(files: dict[str, str]):  # type: ignore[no-untyped-def]
    return rule.check(FakeSnapshot(files))


def manifest(result, path: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    return next(m for m in result.details["manifests"] if m["path"] == path)


def test_not_applicable_without_manifests() -> None:
    result = run({"README.md": "x"})
    assert result.status is Status.NOT_APPLICABLE
    assert result.details == {"manifests": []}


# ---- requirements*.txt ---------------------------------------------------------------------


def test_requirements_all_pinned_passes() -> None:
    result = run({"requirements.txt": "flask==3.0.0\nrequests===2.31.0\n"})
    assert result.status is Status.PASS
    m = manifest(result, "requirements.txt")
    assert m == {
        "path": "requirements.txt",
        "ecosystem": "python",
        "lock_file": None,
        "verdict": "pass",
        "total": 2,
        "unpinned": [],
    }


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("a==1\nb==1\nc==1\nd>=1\n", Status.WARN),  # 25% unpinned
        ("a==1\nb==1\nc>=1\nd>=1\n", Status.WARN),  # exactly 50% is still a warn
        ("a==1\nb>=1\nc>=1\nd>=1\n", Status.FAIL),  # 75%
        ("a\n", Status.FAIL),  # 100%
    ],
)
def test_requirements_unpinned_ratio(text: str, status: Status) -> None:
    assert run({"requirements.txt": text}).status is status


def test_requirements_parsing_details() -> None:
    text = (
        "# a comment\n"
        "\n"
        "-r base.txt\n-c constraints.txt\n-e git+https://x/y#egg=editable\n"
        "-i https://pypi.example/simple\n-f https://wheels\n--extra-index-url https://x\n"
        "--trusted-host x\n"
        "Flask==3.0.0  # pinned, with inline comment\n"
        "requests>=2  ; python_version >= '3.8'\n"  # environment marker stripped
        "Django_REST.framework>=3\n"
        "pinned-with-hash==1.0 \\\n    --hash=sha256:abc \\\n    --hash=sha256:def\n"
        "unpinned-with-hash \\\n    --hash=sha256:abc\n"
    )
    assert parse_requirements(text) == [
        ("flask", True),
        ("requests", False),
        ("django-rest-framework", False),
        ("pinned-with-hash", True),
        ("unpinned-with-hash", False),
    ]


def test_requirements_url_and_vcs_pinning() -> None:
    text = (
        f"pkg1 @ git+https://github.com/o/r.git@{SHA}\n"
        f"git+https://github.com/o/r2.git@{SHA}#egg=pkg2\n"
        "git+https://github.com/o/r3.git@main#egg=pkg3\n"
        "git+https://github.com/o/r4.git@" + "b" * 39 + "#egg=pkg4\n"  # 39 hex chars: not a SHA
    )
    assert parse_requirements(text) == [
        ("pkg1", True),
        ("pkg2", True),
        ("pkg3", False),
        ("pkg4", False),
    ]


def test_hash_inside_a_url_is_not_a_comment() -> None:
    assert parse_requirements("git+https://h/r.git#egg=name\n") == [("name", False)]


def test_requirements_variants_are_all_manifests() -> None:
    files = {
        "requirements-dev.txt": "a>=1",
        "docs/requirements.txt": "b>=1",
        "Requirements.TXT": "c>=1",
    }
    assert len(run(files).details["manifests"]) == 3


def test_empty_requirements_file_is_ignored() -> None:
    assert run({"requirements.txt": "# nothing\n-r other.txt\n"}).status is Status.NOT_APPLICABLE


def test_requirements_never_expose_credentials_in_urls() -> None:
    token = "tok" + "en123SECRET"
    text = (
        f"git+https://user:{token}@github.com/org/private.git\n"
        f"https://user:{token}@host/pkg.tar.gz\n"
        f"git+https://{token}@github.com/org/p2.git#egg=safe_name\n"
    )
    result = run({"requirements.txt": text})
    blob = json.dumps(result.details) + result.message
    assert token not in blob and "user" not in blob and "github.com" not in blob
    assert manifest(result, "requirements.txt")["unpinned"] == ["safe-name", "url-requirement"]


def test_unpinned_names_are_normalised_sorted_deduped_and_capped() -> None:
    text = "\n".join(f"Pkg_{i:02}>=1" for i in range(30)) + "\nPkg_00>=2\n"
    unpinned = manifest(run({"requirements.txt": text}), "requirements.txt")["unpinned"]
    assert len(unpinned) == 20
    assert unpinned == sorted(unpinned) and unpinned[0] == "pkg-00"
    assert normalize_python_name("Foo__Bar.baz") == "foo-bar-baz"


def test_unreadable_manifest_is_a_parse_error_warn() -> None:
    result = rule.check(FakeSnapshot({"requirements.txt": Unreadable(10)}))
    assert result.status is Status.WARN
    assert manifest(result, "requirements.txt")["parse_error"] is True


# ---- pyproject.toml ------------------------------------------------------------------------


PYPROJECT_LOOSE = '[project]\nname = "x"\ndependencies = ["requests>=2", "flask==3.0.0"]\n'


@pytest.mark.parametrize("lock", ["poetry.lock", "uv.lock", "pdm.lock"])
def test_pyproject_with_lock_passes(lock: str) -> None:
    result = run({"pyproject.toml": PYPROJECT_LOOSE, lock: "x"})
    assert result.status is Status.PASS
    m = manifest(result, "pyproject.toml")
    assert m["lock_file"] == lock and m["total"] == 2 and m["unpinned"] == []


def test_pyproject_without_lock_and_unpinned_warns() -> None:
    result = run({"pyproject.toml": PYPROJECT_LOOSE})
    assert result.status is Status.WARN
    assert manifest(result, "pyproject.toml")["unpinned"] == ["requests"]
    assert "No lock file" in result.message


def test_pyproject_all_exact_without_lock_passes() -> None:
    text = (
        '[project]\ndependencies = ["a==1.0", "b[extra]==2.0", "c == 3.0 ; python_version>\'3\'"]'
    )
    assert run({"pyproject.toml": text}).status is Status.PASS


@pytest.mark.parametrize(
    "dep", ["a>=1", "a~=1.0", "a==1.*", "a", "a==1.0,>=0.5 ; x", "a @ https://x/a.whl"]
)
def test_pyproject_non_exact_constraints_are_unpinned(dep: str) -> None:
    text = f'[project]\ndependencies = ["{dep}"]\n'
    if dep == "a==1.0,>=0.5 ; x":
        assert run({"pyproject.toml": text}).status is Status.PASS  # has an == clause
        return
    assert run({"pyproject.toml": text}).status is Status.WARN, dep


def test_pyproject_poetry_dependencies_skip_python_key() -> None:
    text = (
        '[tool.poetry.dependencies]\npython = "^3.12"\nexact = "1.2.3"\ncaret = "^1.0"\n'
        'tilde = "~1.0"\nstar = "*"\nrange = ">=1,<2"\ndict_exact = {version = "2.0.0"}\n'
        'dict_caret = {version = "^2.0"}\ngit_dep = {git = "https://example.com/x.git"}\n'
    )
    m = manifest(run({"pyproject.toml": text}), "pyproject.toml")
    assert m["total"] == 8
    assert m["unpinned"] == ["caret", "dict-caret", "git-dep", "range", "star", "tilde"]


def test_pyproject_project_table_wins_over_poetry_and_no_deps_is_ignored() -> None:
    assert run({"pyproject.toml": '[project]\nname = "x"\n'}).status is Status.NOT_APPLICABLE
    assert run({"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}).status is (
        Status.NOT_APPLICABLE
    )


def test_pyproject_invalid_toml_is_a_parse_error_warn() -> None:
    result = run({"pyproject.toml": "this is [not toml"})
    assert result.status is Status.WARN
    assert manifest(result, "pyproject.toml")["parse_error"] is True


def test_lock_file_must_be_in_the_same_directory() -> None:
    files = {"backend/pyproject.toml": PYPROJECT_LOOSE, "uv.lock": "x"}
    assert run(files).status is Status.WARN
    files = {"backend/pyproject.toml": PYPROJECT_LOOSE, "backend/uv.lock": "x"}
    result = run(files)
    assert result.status is Status.PASS
    assert manifest(result, "backend/pyproject.toml")["lock_file"] == "backend/uv.lock"


# ---- package.json --------------------------------------------------------------------------


def pkg(deps: dict[str, str], dev: dict[str, str] | None = None) -> str:
    return json.dumps({"name": "x", "dependencies": deps, "devDependencies": dev or {}})


@pytest.mark.parametrize(
    "lock",
    [
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "bun.lock",
    ],
)
def test_package_json_with_lock_passes(lock: str) -> None:
    result = run({"package.json": pkg({"a": "^1.0.0"}), lock: "x"})
    assert result.status is Status.PASS
    assert manifest(result, "package.json")["lock_file"] == lock


@pytest.mark.parametrize(
    "version", ["1.2.3", "0.0.1", "1.2.3-beta.1", "1.2.3+build.5", "1.2.3-rc.1+meta"]
)
def test_package_json_exact_versions_pass_without_lock(version: str) -> None:
    assert run({"package.json": pkg({"a": version})}).status is Status.PASS


@pytest.mark.parametrize(
    "version",
    [
        "^1.2.3",
        "~1.2.3",
        "1.2",
        "1",
        "*",
        "latest",
        ">=1.0.0",
        "workspace:*",
        "file:../x",
        "link:../x",
        "git+https://github.com/o/r.git",
        "https://x/y.tgz",
        "npm:other@1.0.0",
        "1.2.x",
        "",
    ],
)
def test_package_json_non_exact_versions_warn(version: str) -> None:
    result = run({"package.json": pkg({"a": version})})
    assert result.status is Status.WARN
    assert manifest(result, "package.json")["unpinned"] == ["a"]


def test_package_json_counts_dev_dependencies_and_lists_names_only() -> None:
    result = run({"package.json": pkg({"a": "1.0.0"}, {"@scope/b": "^2.0.0"})})
    m = manifest(result, "package.json")
    assert m["total"] == 2 and m["unpinned"] == ["@scope/b"]


def test_package_json_urls_with_credentials_are_never_stored() -> None:
    token = "tok" + "en123SECRET"
    text = pkg({"private": f"git+https://user:{token}@github.com/org/repo.git"})
    result = run({"package.json": text})
    assert token not in json.dumps(result.details) + result.message
    assert manifest(result, "package.json")["unpinned"] == ["private"]


def test_package_json_invalid_json_or_no_deps() -> None:
    bad = run({"package.json": "{not json"})
    assert bad.status is Status.WARN and manifest(bad, "package.json")["parse_error"] is True
    assert run({"package.json": '{"name": "x", "scripts": {}}'}).status is Status.NOT_APPLICABLE
    assert run({"package.json": "[1, 2]"}).status is Status.NOT_APPLICABLE


# ---- other ecosystems ----------------------------------------------------------------------


def test_cargo() -> None:
    toml = '[package]\nname = "x"\n[dependencies]\nserde = "1"\n'
    assert run({"Cargo.toml": toml, "Cargo.lock": "x"}).status is Status.PASS
    assert run({"Cargo.toml": toml}).status is Status.WARN
    assert run({"Cargo.toml": '[package]\nname = "x"\n'}).status is Status.NOT_APPLICABLE
    workspace = '[workspace]\nmembers = []\n[workspace.dependencies]\nserde = "1"\n'
    assert run({"Cargo.toml": workspace}).status is Status.WARN
    assert run({"Cargo.toml": "= broken"}).details["manifests"][0]["parse_error"] is True


def test_go_mod() -> None:
    mod = "module x\n\ngo 1.22\n\nrequire (\n\tgithub.com/a/b v1.0.0\n)\n"
    assert run({"go.mod": mod, "go.sum": "x"}).status is Status.PASS
    assert run({"go.mod": mod}).status is Status.FAIL
    assert run({"go.mod": "module x\n\ngo 1.22\n"}).status is Status.NOT_APPLICABLE
    assert run({"go.mod": "module x\nrequire github.com/a/b v1.0.0\n"}).status is Status.FAIL


def test_gemfile_and_pipfile_without_lock_fail() -> None:
    assert run({"Gemfile": "gem 'rails'"}).status is Status.FAIL
    assert run({"Gemfile": "gem 'rails'", "Gemfile.lock": "x"}).status is Status.PASS
    assert run({"Pipfile": "[packages]"}).status is Status.FAIL
    assert run({"Pipfile": "[packages]", "Pipfile.lock": "{}"}).status is Status.PASS
    m = manifest(run({"Pipfile": "[packages]"}), "Pipfile")
    assert m["ecosystem"] == "python" and m["total"] is None and m["unpinned"] == []


# ---- aggregation and discovery -------------------------------------------------------------


def test_overall_result_is_the_worst_manifest_verdict() -> None:
    files = {
        "a/package.json": pkg({"x": "1.0.0"}),
        "b/Gemfile": "gem 'x'",
        "c/pyproject.toml": PYPROJECT_LOOSE,
    }
    result = run(files)
    assert result.status is Status.FAIL
    assert [m["verdict"] for m in result.details["manifests"]] == ["pass", "fail", "warn"]
    assert "2 of 3" in result.message

    del files["b/Gemfile"]
    assert run(files).status is Status.WARN


def test_manifests_are_sorted_by_path() -> None:
    files = {"z/Gemfile": "g", "a/Gemfile": "g", "Gemfile": "g"}
    assert [m["path"] for m in run(files).details["manifests"]] == [
        "Gemfile",
        "a/Gemfile",
        "z/Gemfile",
    ]


def test_search_depth_is_two_directories() -> None:
    assert run({"a/b/Gemfile": "g"}).status is Status.FAIL
    assert run({"a/b/c/Gemfile": "g"}).status is Status.NOT_APPLICABLE


def test_ignored_directories_are_not_searched() -> None:
    files = {"node_modules/pkg/package.json": pkg({"a": "^1"}), "vendor/Gemfile": "g"}
    assert run(files).status is Status.NOT_APPLICABLE


def test_ruby_and_python_manifest_names_are_case_insensitive() -> None:
    assert run({"GEMFILE": "g"}).status is Status.FAIL
    assert run({"pipfile": "[packages]"}).status is Status.FAIL


def test_metadata() -> None:
    assert (rule.id, rule.weight, rule.display_order) == ("dependencies", 10, 60)
    assert "uv lock" in rule.fix_tip
