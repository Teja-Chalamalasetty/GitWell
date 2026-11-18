from __future__ import annotations

import logging

import pytest
import respx

from app.github.client import GitHubClient
from app.github.errors import GitHubError, GitHubNotFound, GitHubUnavailable
from app.scanner.archive_snapshot import SnapshotLimits, build_archive_snapshot
from app.scanner.deadline import Deadline, ScanTimeout
from tests.fakes import chunked, make_tarball
from tests.github.conftest import BASE

SHA = "a" * 40
API_TARBALL = f"{BASE}/repos/o/r/tarball/{SHA}"
TOKEN_QUERY = "tok" + "en=SHORTLIVEDSECRET123"
CODELOAD = f"https://codeload.github.com/o/r/legacy.tar.gz/{SHA}"
CODELOAD_WITH_TOKEN = f"{CODELOAD}?{TOKEN_QUERY}"
TARBALL = make_tarball({"README.md": "# hi", "src/a.py": "x = 1"})
LIMITS = SnapshotLimits(50 * 1024**2, 500 * 1024**2, 100_000, 524_288, 200 * 1024**2, 40)


def stream(client: GitHubClient, deadline: Deadline) -> bytes:
    return b"".join(client.stream_tarball("o", "r", SHA, deadline))


def test_302_to_codeload_is_followed_and_codeload_gets_no_authorization(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    api = mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD_WITH_TOKEN})
    code = mock.get(url__startswith=CODELOAD).respond(200, content=TARBALL)

    assert stream(client, deadline) == TARBALL

    assert api.calls.last.request.headers["authorization"] == "Bearer tkn"
    codeload_request = code.calls.last.request
    assert "authorization" not in codeload_request.headers
    assert codeload_request.url.params["token"] == "SHORTLIVEDSECRET123"  # URL used verbatim
    assert codeload_request.headers["user-agent"] == "GitWell/1.0"


def test_the_short_lived_token_appears_in_no_log_record(
    mock: respx.MockRouter,
    client: GitHubClient,
    deadline: Deadline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)  # even at DEBUG, nothing may leak
    mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD_WITH_TOKEN})
    mock.get(url__startswith=CODELOAD).respond(200, content=TARBALL)
    stream(client, deadline)
    everything = " ".join(f"{r.getMessage()} {r.__dict__} {r.exc_text}" for r in caplog.records)
    assert "SHORTLIVEDSECRET123" not in everything
    assert "codeload.github.com" not in everything
    assert "codeload tarball" in everything  # a route template, not a URL


def test_the_token_is_not_in_errors_either(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD_WITH_TOKEN})
    mock.get(url__startswith=CODELOAD).respond(500)
    with pytest.raises(GitHubUnavailable) as exc:
        stream(client, deadline)
    assert "SHORTLIVEDSECRET123" not in repr(exc.value) + str(exc.value.details)


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/o/r.tar.gz",
        "https://codeload.github.com.evil.example/o/r",  # suffix trick
        "https://evilcodeload.github.com/o/r",  # prefix trick
        "https://github.com/o/r/archive.tar.gz",  # right company, wrong host
        "https://api.github.com/repos/o/r/tarball/x",
        "http://codeload.github.com/o/r",  # scheme downgrade
        "https://user:pw@codeload.github.com/o/r",  # credentials
        "https://codeload.github.com:8443/o/r",  # unexpected port
        "ftp://codeload.github.com/o/r",
        "//evil.example/o/r",  # protocol-relative
    ],
)
def test_location_must_be_https_codeload_exactly(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, location: str
) -> None:
    mock.get(API_TARBALL).respond(302, headers={"Location": location})
    with pytest.raises(GitHubError):
        client.stream_tarball("o", "r", SHA, deadline)
    assert len(mock.calls) == 1  # nothing was ever sent to the bad host


def test_port_443_is_accepted(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(API_TARBALL).respond(
        302, headers={"Location": "https://codeload.github.com:443/o/r/legacy.tar.gz/x"}
    )
    mock.get(url__startswith="https://codeload.github.com").respond(200, content=TARBALL)
    assert stream(client, deadline) == TARBALL


def test_missing_location_is_an_error(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(API_TARBALL).respond(302)
    with pytest.raises(GitHubError):
        client.stream_tarball("o", "r", SHA, deadline)


def test_a_200_instead_of_a_redirect_is_an_error(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(API_TARBALL).respond(200, content=TARBALL)
    with pytest.raises(GitHubError):
        client.stream_tarball("o", "r", SHA, deadline)


def test_api_errors_are_classified_before_streaming_starts(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(API_TARBALL).respond(404)
    with pytest.raises(GitHubNotFound):
        client.stream_tarball("o", "r", SHA, deadline)  # raises at call time, not on iteration


def test_one_redirect_at_codeload_is_followed_but_not_two(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD})
    mock.get(CODELOAD).respond(302, headers={"Location": "https://codeload.github.com/o/r/second"})
    second = mock.get("https://codeload.github.com/o/r/second")
    second.respond(200, content=TARBALL)
    assert stream(client, deadline) == TARBALL

    second.respond(302, headers={"Location": "https://codeload.github.com/o/r/third"})
    with pytest.raises(GitHubError):
        stream(client, deadline)


def test_codeload_redirect_to_a_foreign_host_is_rejected(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD})
    mock.get(CODELOAD).respond(302, headers={"Location": "https://evil.example/x"})
    with pytest.raises(GitHubError):
        stream(client, deadline)
    assert len(mock.calls) == 2


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, GitHubNotFound),
        (410, GitHubNotFound),
        (500, GitHubUnavailable),
        (503, GitHubUnavailable),
        (403, GitHubError),
    ],
)
def test_codeload_error_statuses(
    mock: respx.MockRouter,
    client: GitHubClient,
    deadline: Deadline,
    status: int,
    error: type[GitHubError],
) -> None:
    mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD})
    mock.get(CODELOAD).respond(status)
    with pytest.raises(error):
        stream(client, deadline)


def test_codeload_network_error_is_unavailable(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    import httpx

    mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD})
    mock.get(CODELOAD).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(GitHubUnavailable):
        stream(client, deadline)


def test_deadline_is_checked_between_chunks(mock: respx.MockRouter, client: GitHubClient) -> None:
    d = Deadline(60)
    mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD})
    mock.get(CODELOAD).respond(200, content=b"x" * 200_000)
    chunks = client.stream_tarball("o", "r", SHA, d)
    next(chunks)
    d.expire()
    with pytest.raises(ScanTimeout):
        next(chunks)


def test_end_to_end_stream_into_an_archive_snapshot(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(API_TARBALL).respond(302, headers={"Location": CODELOAD_WITH_TOKEN})
    mock.get(url__startswith=CODELOAD).respond(200, content=TARBALL)
    snap = build_archive_snapshot(
        client.stream_tarball("o", "r", SHA, deadline),
        full_name="o/r",
        default_branch="main",
        commit_sha=SHA,
        limits=LIMITS,
        deadline=deadline,
    )
    assert [e.path for e in snap.files()] == ["README.md", "src/a.py"]
    assert snap.read_text("src/a.py") == "x = 1"


def test_chunked_helper_roundtrip() -> None:
    assert b"".join(chunked(TARBALL, 100)) == TARBALL
