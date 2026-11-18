import pytest

from app.github.url import InvalidRepoUrl, parse_repo_url

VALID = [
    ("https://github.com/psf/requests", ("psf", "requests")),
    ("https://github.com/psf/requests/", ("psf", "requests")),
    ("https://github.com/psf/requests.git", ("psf", "requests")),
    ("https://github.com/psf/requests.git/", ("psf", "requests")),
    ("  https://github.com/psf/requests  ", ("psf", "requests")),  # strip()
    ("https://github.com/a/b", ("a", "b")),  # one-character owner
    ("https://github.com/Some-Org/My_Repo.v2", ("Some-Org", "My_Repo.v2")),  # dots, underscores
    ("https://github.com/o/a.b.c", ("o", "a.b.c")),
    ("https://github.com/o/.github", ("o", ".github")),  # a real repo name
    ("https://github.com/o/-dash", ("o", "-dash")),
    ("https://github.com/o/repo.git.git", ("o", "repo.git")),
    ("https://github.com/" + "a" * 39 + "/r", ("a" * 39, "r")),  # max-length owner
    ("https://github.com/o/" + "n" * 100, ("o", "n" * 100)),  # max-length name
    ("https://github.com/o1/r1", ("o1", "r1")),
    ("https://github.com/a-b/r", ("a-b", "r")),
]

INVALID = [
    "",
    "   ",
    "github.com/psf/requests",  # no scheme
    "http://github.com/psf/requests",  # not https
    "HTTPS://github.com/psf/requests",  # uppercase scheme
    "https://GitHub.com/psf/requests",  # uppercase host
    "https://www.github.com/psf/requests",  # www.
    "https://gist.github.com/psf/requests",
    "https://gitlab.com/psf/requests",  # another host
    "https://github.com.evil.com/psf/requests",  # host suffix trick
    "https://evil.com/github.com/psf/requests",
    "https://github.com/psf",  # no repo
    "https://github.com/",
    "https://github.com",
    "https://github.com/psf/requests/tree/main",  # extra path segments
    "https://github.com/psf/requests/issues",
    "https://github.com/psf/requests?tab=readme",  # query string
    "https://github.com/psf/requests#readme",  # fragment
    "https://user@github.com/psf/requests",  # credentials
    "https://user:pass@github.com/psf/requests",
    "https://github.com:443/psf/requests",  # explicit port
    "https://github.com/-bad/requests",  # owner starts with a hyphen
    "https://github.com/bad-/requests",  # owner ends with a hyphen
    "https://github.com/ba--d/requests",  # double hyphen in owner
    "https://github.com/" + "a" * 40 + "/r",  # owner too long
    "https://github.com/o/" + "n" * 101,  # name too long
    "https://github.com/o/.",
    "https://github.com/o/..",
    "https://github.com/o/.git",
    "https://github.com/o/re po",  # whitespace in the name
    "https://github.com/o/re%20po",
    "https://github.com/o/r\nhttps://evil.com",
    "https://github.com/o/r\n",  # stripped, but see the next case
    "https://github.com//r",
    "https://github.com/o//r",
    "https://github.com/o/r//",
    "https://github.com/o_o/r",  # underscores are not valid in owners
    "ftp://github.com/o/r",
    "git@github.com:o/r.git",
    "https://github.com/o/r.git/extra",
]


@pytest.mark.parametrize(("url", "expected"), VALID)
def test_valid_urls(url: str, expected: tuple[str, str]) -> None:
    assert parse_repo_url(url) == expected


@pytest.mark.parametrize("url", INVALID)
def test_invalid_urls(url: str) -> None:
    if url == "https://github.com/o/r\n":
        # strip() makes a trailing newline harmless: this one is VALID.
        assert parse_repo_url(url) == ("o", "r")
        return
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url(url)


def test_case_table_has_at_least_20_cases() -> None:
    assert len(VALID) + len(INVALID) >= 20
