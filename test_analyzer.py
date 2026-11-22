"""Unit tests for github_analyzer. All GitHub API traffic is mocked."""

import os
import sys
from unittest.mock import MagicMock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import github_analyzer as ga  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_response(status=200, body=None, headers=None, json_error=False):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    if json_error:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = body if body is not None else {}
    return response


class FakeSession:
    """Routes GET requests by API path and records what was requested."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        path = url.replace(ga.GITHUB_API, "")
        self.calls.append((path, headers, timeout))
        if path not in self.routes:
            return make_response(404, {"message": "Not Found"})
        result = self.routes[path]
        if isinstance(result, Exception):
            raise result
        return result


REPO_JSON = {
    "name": "demo",
    "full_name": "octo/demo",
    "html_url": "https://github.com/octo/demo",
    "description": "A demo repository",
    "owner": {"login": "octo"},
    "created_at": "2023-01-15T10:00:00Z",
    "updated_at": "2024-03-20T08:30:00Z",
    "pushed_at": "2024-03-19T08:30:00Z",
    "stargazers_count": 42,
    "forks_count": 7,
    "open_issues_count": 3,
    "size": 2048,
    "default_branch": "main",
    "license": {"name": "MIT License"},
    "archived": False,
    "fork": False,
}

FULL_ROOT = [
    {"name": "README.md", "type": "file"},
    {"name": "requirements.txt", "type": "file"},
    {"name": "LICENSE", "type": "file"},
    {"name": ".gitignore", "type": "file"},
    {"name": "tests", "type": "dir"},
    {"name": ".github", "type": "dir"},
    {"name": "app.py", "type": "file"},
]


def build_session(repo=REPO_JSON, languages=None, root=None, workflows=None):
    routes = {
        "/repos/octo/demo": make_response(200, repo),
        "/repos/octo/demo/languages": make_response(
            200, {"Python": 7500, "HTML": 2500} if languages is None else languages
        ),
        "/repos/octo/demo/contents": make_response(200, FULL_ROOT if root is None else root),
    }
    if root is None or any(e["name"] == ".github" for e in root):
        routes["/repos/octo/demo/contents/.github/workflows"] = make_response(
            200, [{"name": "ci.yml", "type": "file"}] if workflows is None else workflows
        )
    return FakeSession(routes)


def checks_by_key(checks):
    return {check.key: check for check in checks}


# --------------------------------------------------------------------------- #
# URL validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octo/demo",
        "http://github.com/octo/demo",
        "https://www.github.com/octo/demo",
        "github.com/octo/demo",
        "https://github.com/octo/demo/",
        "https://github.com/octo/demo.git",
        "https://github.com/octo/demo/tree/main/src",
        "https://github.com/octo/demo?tab=readme-ov-file",
        "  https://github.com/octo/demo  ",
    ],
)
def test_parse_valid_urls(url):
    assert ga.parse_repository_url(url) == ("octo", "demo")


def test_parse_keeps_dots_and_hyphens_in_repo_name():
    assert ga.parse_repository_url("https://github.com/my-org/my.repo-name_v2") == (
        "my-org", "my.repo-name_v2",
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        None,
        123,
        "not a url",
        "https://gitlab.com/octo/demo",
        "https://github.com/octo",
        "https://github.com/",
        "https://github.com.evil.com/octo/demo",
        "https://example.com/github.com/octo/demo",
        "https://github.com/octo/..",
        "https://github.com/-octo/demo",
    ],
)
def test_parse_invalid_urls(url):
    with pytest.raises(ga.InvalidURLError):
        ga.parse_repository_url(url)


# --------------------------------------------------------------------------- #
# Formatting and language statistics
# --------------------------------------------------------------------------- #
def test_format_date():
    assert ga.format_date("2024-03-20T08:30:00Z") == "20 Mar 2024"
    assert ga.format_date("") == "Unknown"
    assert ga.format_date("garbage") == "garbage"


def test_format_size():
    assert ga.format_size(512) == "512 KB"
    assert ga.format_size(2048) == "2.0 MB"


def test_language_stats_percentages_and_order():
    stats = ga.calculate_language_stats({"HTML": 2500, "Python": 7500})
    assert [s.name for s in stats] == ["Python", "HTML"]
    assert [s.percentage for s in stats] == [75.0, 25.0]
    assert sum(s.percentage for s in stats) == pytest.approx(100.0)


def test_language_stats_empty():
    assert ga.calculate_language_stats({}) == []
    assert ga.calculate_language_stats({"Python": 0}) == []


# --------------------------------------------------------------------------- #
# API client: response handling and errors
# --------------------------------------------------------------------------- #
def test_get_repository_success():
    client = ga.GitHubClient(session=FakeSession({"/repos/octo/demo": make_response(200, REPO_JSON)}))
    assert client.get_repository("octo", "demo")["full_name"] == "octo/demo"


def test_client_sends_token_and_timeout():
    session = FakeSession({"/repos/octo/demo": make_response(200, REPO_JSON)})
    client = ga.GitHubClient(token=" abc123 ", session=session, timeout=5)
    client.get_repository("octo", "demo")
    _, headers, timeout = session.calls[0]
    assert headers["Authorization"] == "Bearer abc123"
    assert timeout == 5


def test_client_without_token_sends_no_authorization():
    session = FakeSession({"/repos/octo/demo": make_response(200, REPO_JSON)})
    ga.GitHubClient(session=session).get_repository("octo", "demo")
    assert "Authorization" not in session.calls[0][1]


def test_rate_limit_headers_are_recorded():
    headers = {"X-RateLimit-Remaining": "55", "X-RateLimit-Limit": "60"}
    session = FakeSession({"/repos/octo/demo": make_response(200, REPO_JSON, headers)})
    client = ga.GitHubClient(session=session)
    client.get_repository("octo", "demo")
    assert (client.rate_limit_remaining, client.rate_limit_total) == (55, 60)


def test_not_found_raises():
    session = FakeSession({"/repos/octo/demo": make_response(404, {"message": "Not Found"})})
    with pytest.raises(ga.RepositoryNotFoundError):
        ga.GitHubClient(session=session).get_repository("octo", "demo")


def test_rate_limit_403_with_zero_remaining():
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1893456000"}
    session = FakeSession({"/repos/octo/demo": make_response(403, {"message": "Forbidden"}, headers)})
    with pytest.raises(ga.RateLimitError) as info:
        ga.GitHubClient(session=session).get_repository("octo", "demo")
    assert "resets at" in str(info.value)


def test_rate_limit_403_detected_from_message():
    body = {"message": "API rate limit exceeded for 1.2.3.4"}
    session = FakeSession({"/repos/octo/demo": make_response(403, body)})
    with pytest.raises(ga.RateLimitError):
        ga.GitHubClient(session=session).get_repository("octo", "demo")


def test_rate_limit_429():
    session = FakeSession({"/repos/octo/demo": make_response(429, {"message": "slow down"})})
    with pytest.raises(ga.RateLimitError):
        ga.GitHubClient(session=session).get_repository("octo", "demo")


def test_other_403_is_generic_api_error():
    session = FakeSession({"/repos/octo/demo": make_response(403, {"message": "Blocked"})})
    with pytest.raises(ga.GitHubAPIError) as info:
        ga.GitHubClient(session=session).get_repository("octo", "demo")
    assert not isinstance(info.value, ga.RateLimitError)


def test_bad_token_401():
    session = FakeSession({"/repos/octo/demo": make_response(401, {"message": "Bad credentials"})})
    with pytest.raises(ga.GitHubAPIError, match="token"):
        ga.GitHubClient(token="bad", session=session).get_repository("octo", "demo")


def test_server_error_500():
    session = FakeSession({"/repos/octo/demo": make_response(500, {"message": "Oops"})})
    with pytest.raises(ga.GitHubAPIError, match="500"):
        ga.GitHubClient(session=session).get_repository("octo", "demo")


def test_unreadable_json_raises_api_error():
    session = FakeSession({"/repos/octo/demo": make_response(200, json_error=True)})
    with pytest.raises(ga.GitHubAPIError):
        ga.GitHubClient(session=session).get_repository("octo", "demo")


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError("dns failure"),
        requests.exceptions.Timeout("slow"),
        requests.exceptions.RequestException("other"),
    ],
)
def test_network_errors(error):
    session = FakeSession({"/repos/octo/demo": error})
    with pytest.raises(ga.NetworkError):
        ga.GitHubClient(session=session).get_repository("octo", "demo")


def test_list_directory_404_returns_empty_list():
    session = FakeSession({"/repos/octo/demo/contents": make_response(404, {"message": "This repository is empty."})})
    assert ga.GitHubClient(session=session).list_directory("octo", "demo") == []


def test_list_directory_rate_limit_is_not_swallowed():
    headers = {"X-RateLimit-Remaining": "0"}
    session = FakeSession({"/repos/octo/demo/contents": make_response(403, {"message": "x"}, headers)})
    with pytest.raises(ga.RateLimitError):
        ga.GitHubClient(session=session).list_directory("octo", "demo")


# --------------------------------------------------------------------------- #
# Structure checks
# --------------------------------------------------------------------------- #
WORKFLOW = [{"name": "ci.yml", "type": "file"}]


def test_all_checks_found():
    checks = checks_by_key(ga.run_structure_checks(FULL_ROOT, WORKFLOW))
    assert len(checks) == 6
    assert all(check.found for check in checks.values())
    assert checks["readme"].evidence == "README.md"
    assert checks["tests"].evidence == "tests/"
    assert ".github/workflows/ci.yml" in checks["workflows"].evidence


def test_no_checks_found_in_minimal_repo():
    checks = ga.run_structure_checks([{"name": "main.c", "type": "file"}], [])
    assert not any(check.found for check in checks)
    assert all(check.suggestion and check.why_it_matters for check in checks)


@pytest.mark.parametrize("name", ["README", "readme.rst", "Readme.md", "README.txt"])
def test_readme_variants(name):
    checks = checks_by_key(ga.run_structure_checks([{"name": name, "type": "file"}], []))
    assert checks["readme"].found


@pytest.mark.parametrize(
    "name", ["package.json", "pyproject.toml", "requirements-dev.txt", "Cargo.toml", "go.mod", "App.csproj"]
)
def test_dependency_file_variants(name):
    checks = checks_by_key(ga.run_structure_checks([{"name": name, "type": "file"}], []))
    assert checks["dependencies"].found


def test_tests_must_be_a_directory():
    checks = checks_by_key(ga.run_structure_checks([{"name": "tests", "type": "file"}], []))
    assert not checks["tests"].found


@pytest.mark.parametrize("name", ["test", "tests", "spec", "__tests__"])
def test_test_directory_variants(name):
    checks = checks_by_key(ga.run_structure_checks([{"name": name, "type": "dir"}], []))
    assert checks["tests"].found


def test_license_detected_by_github_when_file_missing():
    checks = checks_by_key(ga.run_structure_checks([], [], license_name="MIT License"))
    assert checks["license"].found
    assert "MIT License" in checks["license"].evidence


def test_workflow_requires_yaml_files():
    not_yaml = [{"name": "notes.txt", "type": "file"}]
    assert not checks_by_key(ga.run_structure_checks([], not_yaml))["workflows"].found
    assert checks_by_key(ga.run_structure_checks([], [{"name": "x.yaml", "type": "file"}]))["workflows"].found


# --------------------------------------------------------------------------- #
# Full analysis
# --------------------------------------------------------------------------- #
def test_full_analysis_with_complete_repository():
    session = build_session()
    result = ga.analyze_repository("https://github.com/octo/demo", client=ga.GitHubClient(session=session))

    info = result.repository
    assert (info.full_name, info.owner, info.stars, info.forks) == ("octo/demo", "octo", 42, 7)
    assert info.open_issues == 3 and info.size_kb == 2048
    assert [(s.name, s.percentage) for s in result.languages] == [("Python", 75.0), ("HTML", 25.0)]
    assert result.found_count == 6
    assert result.suggestions == []
    assert not result.is_empty
    assert "6 of 6" in result.summary


def test_analysis_reports_missing_files_and_suggestions():
    root = [{"name": "main.py", "type": "file"}]
    repo = dict(REPO_JSON, license=None)
    session = build_session(repo=repo, root=root)
    result = ga.analyze_repository("https://github.com/octo/demo", client=ga.GitHubClient(session=session))

    missing = {check.key for check in result.missing_checks}
    assert missing == {"readme", "dependencies", "tests", "license", "gitignore", "workflows"}
    assert len(result.suggestions) == 6
    assert "0 of 6" in result.summary
    # No .github directory in the root, so the workflows endpoint must not be called.
    assert all("workflows" not in path for path, _, _ in session.calls)


def test_empty_repository():
    session = FakeSession(
        {
            "/repos/octo/demo": make_response(200, REPO_JSON),
            "/repos/octo/demo/languages": make_response(200, {}),
            "/repos/octo/demo/contents": make_response(404, {"message": "This repository is empty."}),
        }
    )
    result = ga.analyze_repository("https://github.com/octo/demo", client=ga.GitHubClient(session=session))
    assert result.is_empty
    assert result.languages == []
    assert result.found_count == 1  # only the license, which GitHub's metadata reports
    assert result.checks[3].key == "license" and result.checks[3].found
    assert "no files" in result.summary
    assert any("initial commit" in s for s in result.suggestions)


def test_missing_description_and_no_languages():
    repo = dict(REPO_JSON, description=None)
    session = build_session(repo=repo, languages={})
    result = ga.analyze_repository("https://github.com/octo/demo", client=ga.GitHubClient(session=session))
    assert result.repository.description == ""
    assert any("description" in s for s in result.suggestions)
    assert any("no language data" in s for s in result.suggestions)


def test_analysis_propagates_invalid_url_without_network_call():
    session = FakeSession({})
    with pytest.raises(ga.InvalidURLError):
        ga.analyze_repository("https://example.com/x/y", client=ga.GitHubClient(session=session))
    assert session.calls == []


def test_analysis_propagates_not_found():
    session = FakeSession({"/repos/octo/demo": make_response(404, {"message": "Not Found"})})
    with pytest.raises(ga.RepositoryNotFoundError):
        ga.analyze_repository("https://github.com/octo/demo", client=ga.GitHubClient(session=session))


def test_analysis_propagates_rate_limit_from_later_call():
    session = FakeSession(
        {
            "/repos/octo/demo": make_response(200, REPO_JSON),
            "/repos/octo/demo/languages": make_response(403, {"message": "API rate limit exceeded"}),
        }
    )
    with pytest.raises(ga.RateLimitError):
        ga.analyze_repository("https://github.com/octo/demo", client=ga.GitHubClient(session=session))


# --------------------------------------------------------------------------- #
# Markdown report
# --------------------------------------------------------------------------- #
def test_report_for_complete_repository():
    session = build_session()
    result = ga.analyze_repository("https://github.com/octo/demo", client=ga.GitHubClient(session=session))
    report = ga.generate_markdown_report(result)
    assert report.startswith("# Repository analysis: octo/demo")
    assert "| Stars | 42 |" in report
    assert "| Python | 7,500 | 75.00% |" in report
    assert "| README file | Found |" in report
    assert "Not found |" not in report
    assert "does not measure code quality" in report


def test_report_lists_missing_items_and_suggestions():
    repo = dict(REPO_JSON, description=None, license=None)
    session = build_session(repo=repo, root=[{"name": "main.py", "type": "file"}])
    result = ga.analyze_repository("https://github.com/octo/demo", client=ga.GitHubClient(session=session))
    report = ga.generate_markdown_report(result)
    assert "No description provided" in report
    assert "| License file | Not found |" in report
    assert "Why the missing items matter" in report
    assert "- Add a README.md" in report
