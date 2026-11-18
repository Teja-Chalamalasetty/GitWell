"""S7: secret values are never persisted, returned or logged.

Planted: a real-looking key in a tarball, plus credentials inside a requirements.txt URL and a
package.json URL. After a full scan, none of them may appear in any database row, any API
response, or any captured log record. (M6 extends this to the generated issue body.)
All secret-shaped strings are built at runtime so this repository never contains one.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import Base
from tests.fake_github import FakeGitHub

V1 = "/api/v1"

AWS_KEY = "AKIA" + "Q" * 16
GH_TOKEN = "gh" + "p_" + "z" * 36
SLACK = "xox" + "b-" + "9" * 12
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
URL_TOKEN = "tok" + "en-in-a-url-0123456789"
URL_USER = "deploy" + "-bot"
PIP_URL = f"git+https://{URL_USER}:{URL_TOKEN}@github.com/acme/private-lib.git"
NPM_URL = f"git+https://{URL_USER}:{URL_TOKEN}@github.com/acme/private-ui.git"
GENERIC_VALUE = "s3cr3t" + "-generic-value-42"
ENV_VALUE = "env" + "-file-secret-value-777"

PLANTED = [AWS_KEY, GH_TOKEN, SLACK, PEM, URL_TOKEN, URL_USER, GENERIC_VALUE, ENV_VALUE]

FILES = {
    "src/settings.py": f"AWS = '{AWS_KEY}'\nSLACK = '{SLACK}'\n",
    "deploy/key.txt": f"{PEM}\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n",
    "config/app.yml": "pass" + f'word: "{GENERIC_VALUE}"\n',
    ".env": f"API_TOKEN={GH_TOKEN}\nOTHER={ENV_VALUE}\n",
    "requirements.txt": f"flask==3.0.0\n{PIP_URL}\nrequests>=2\n",
    "package.json": json.dumps({"dependencies": {"private-ui": NPM_URL, "left-pad": "^1.3.0"}}),
    "README.md": "# demo\n" + "text " * 60,
}


def dump_database(db: Session) -> str:
    rows: list[str] = []
    for table in Base.metadata.sorted_tables:
        for row in db.execute(text(f"SELECT * FROM {table.name}")):  # noqa: S608
            rows.append(f"{table.name}: {tuple(row)!r}")
    return "\n".join(rows)


def test_no_secret_value_is_stored_returned_or_logged(
    capsys: pytest.CaptureFixture[str],
    client: TestClient,
    gh: FakeGitHub,
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    gh.repo.files = FILES
    added = client.post(
        f"{V1}/repos", headers=admin_headers, json={"url": "https://github.com/o/r"}
    )
    assert added.status_code == 201
    repo = added.json()
    scan_id = repo["latest_completed_scan"]["id"]
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)  # a second scan, too

    responses = [
        added.text,
        client.get(f"{V1}/repos", headers=admin_headers).text,
        client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).text,
        client.get(f"{V1}/repos/{repo['id']}/scans", headers=admin_headers).text,
        client.get(f"{V1}/scans/{scan_id}", headers=admin_headers).text,
        client.get(f"{V1}/rules", headers=admin_headers).text,
        client.get(f"{V1}/users", headers=admin_headers).text,
    ]
    captured = capsys.readouterr()
    haystacks = {
        "database": dump_database(db),
        "api responses": "\n".join(responses),
        "log output (stdout)": captured.out,
        "log output (stderr)": captured.err,
    }
    for where, text_ in haystacks.items():
        for secret in PLANTED:
            assert secret not in text_, f"{secret[:6]}... leaked into {where}"

    # The test is not vacuous: the scan really did find and report these locations.
    detail = client.get(f"{V1}/scans/{scan_id}", headers=admin_headers).json()
    secrets = next(r for r in detail["results"] if r["rule_id"] == "secrets")
    assert secrets["status"] == "fail"
    found = {(f["path"], f["pattern"]) for f in secrets["details"]["findings"]}
    assert ("src/settings.py", "aws_access_key_id") in found
    assert ("src/settings.py", "slack_token") in found
    assert ("deploy/key.txt", "private_key") in found
    assert ("config/app.yml", "generic_secret_assignment") in found
    assert (".env", "committed_env_file") in found
    assert (".env", "github_token") in found
    assert all(
        set(f) == {"path", "line", "pattern", "confidence"} for f in secrets["details"]["findings"]
    )

    deps = next(r for r in detail["results"] if r["rule_id"] == "dependencies")
    assert deps["status"] == "fail"  # 2 of 3 requirements unpinned: over 50%
    by_path = {m["path"]: m for m in deps["details"]["manifests"]}
    assert by_path["requirements.txt"]["unpinned"] == ["requests", "url-requirement"]
    assert by_path["package.json"]["unpinned"] == ["left-pad", "private-ui"]  # names only


def test_a_failing_scan_does_not_leak_anything_either(
    capsys: pytest.CaptureFixture[str],
    client: TestClient,
    gh: FakeGitHub,
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    gh.repo.files = FILES
    repo = client.post(
        f"{V1}/repos", headers=admin_headers, json={"url": "https://github.com/o/r"}
    ).json()
    gh.repo.empty = True
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    everything = dump_database(db) + capsys.readouterr().out
    assert not any(secret in everything for secret in PLANTED)
