"""Core logic for the GitHub Repository Quality Analyzer.

This module has no dependency on Streamlit. It validates GitHub URLs, talks to
the GitHub REST API, runs a fixed checklist of repository-structure checks and
renders a Markdown report. Everything the checklist reports is derived from
real API responses.

Methodology (kept deliberately simple and transparent):
    * Language percentages = bytes of code per language / total bytes reported
      by the ``/languages`` endpoint.
    * Structure checks = presence of well-known file or directory names in the
      repository root (plus ``.github/workflows``). A check is "Found" only when
      a matching entry is actually returned by the API.
    * No score is computed for code quality. The checklist reports how many of
      the six recommended items were found, nothing more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 10

_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$",
    re.IGNORECASE,
)

DEPENDENCY_FILE_NAMES = {
    "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "pipfile",
    "environment.yml", "environment.yaml", "poetry.lock", "package.json",
    "pom.xml", "build.gradle", "build.gradle.kts", "cargo.toml", "go.mod",
    "gemfile", "composer.json", "cmakelists.txt", "pubspec.yaml",
    "mix.exs", "build.sbt", "package.swift",
}
TEST_DIRECTORY_NAMES = {"test", "tests", "spec", "specs", "__tests__", "testing"}
WORKFLOW_DIRECTORY = ".github/workflows"


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class AnalyzerError(Exception):
    """Base class for all errors raised by this module."""


class InvalidURLError(AnalyzerError):
    """The text entered is not a valid GitHub repository URL."""


class RepositoryNotFoundError(AnalyzerError):
    """The repository does not exist or is not publicly accessible."""


class RateLimitError(AnalyzerError):
    """The GitHub API rate limit has been exhausted."""


class NetworkError(AnalyzerError):
    """GitHub could not be reached (timeout, DNS failure, no connection)."""


class GitHubAPIError(AnalyzerError):
    """GitHub returned an unexpected response."""


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #
@dataclass
class RepositoryInfo:
    owner: str
    name: str
    full_name: str
    html_url: str
    description: str
    created_at: str
    updated_at: str
    pushed_at: str
    stars: int
    forks: int
    open_issues: int
    size_kb: int
    default_branch: str
    license_name: Optional[str]
    archived: bool
    is_fork: bool


@dataclass
class LanguageStat:
    name: str
    bytes: int
    percentage: float


@dataclass
class CheckResult:
    key: str
    label: str
    found: bool
    evidence: str
    why_it_matters: str
    suggestion: str


@dataclass
class AnalysisResult:
    repository: RepositoryInfo
    languages: list[LanguageStat]
    checks: list[CheckResult]
    root_entries: list[dict]
    is_empty: bool
    suggestions: list[str] = field(default_factory=list)
    summary: str = ""
    analyzed_at: str = ""

    @property
    def found_count(self) -> int:
        return sum(1 for check in self.checks if check.found)

    @property
    def missing_checks(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.found]


# --------------------------------------------------------------------------- #
# URL handling and formatting helpers
# --------------------------------------------------------------------------- #
def parse_repository_url(url: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` from a GitHub repository URL.

    Accepts ``https://github.com/owner/repo`` with an optional ``www.``,
    ``.git`` suffix, trailing slash, sub-path (``/tree/main``), query string
    or fragment. Raises ``InvalidURLError`` for anything else.
    """
    if not isinstance(url, str) or not url.strip():
        raise InvalidURLError("Enter a GitHub repository URL.")

    cleaned = url.strip().split("?", 1)[0].split("#", 1)[0]
    match = _URL_PATTERN.match(cleaned)
    if not match:
        raise InvalidURLError(
            "That does not look like a GitHub repository URL. "
            "Use the format https://github.com/owner/repository."
        )

    owner, repo = match.group("owner"), match.group("repo")
    if repo in {".", ".."}:
        raise InvalidURLError("The repository name in that URL is not valid.")
    return owner, repo


def format_date(iso_timestamp: str) -> str:
    """Convert a GitHub ISO timestamp to e.g. ``14 Mar 2024``."""
    if not iso_timestamp:
        return "Unknown"
    try:
        parsed = datetime.strptime(iso_timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return iso_timestamp
    return parsed.strftime("%d %b %Y")


def format_size(size_kb: int) -> str:
    """Format the size GitHub reports (in KB) as a readable string."""
    if size_kb < 1024:
        return f"{size_kb:,} KB"
    return f"{size_kb / 1024:,.1f} MB"


# --------------------------------------------------------------------------- #
# GitHub API client
# --------------------------------------------------------------------------- #
class GitHubClient:
    """Small wrapper around the GitHub REST API with explicit error handling."""

    def __init__(self, token: Optional[str] = None,
                 session: Optional[requests.Session] = None,
                 timeout: int = REQUEST_TIMEOUT_SECONDS):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.token = token.strip() if token and token.strip() else None
        self.rate_limit_remaining: Optional[int] = None
        self.rate_limit_total: Optional[int] = None

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-repository-quality-analyzer",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, path: str) -> requests.Response:
        try:
            response = self.session.get(
                f"{GITHUB_API}{path}", headers=self._headers(), timeout=self.timeout
            )
        except requests.exceptions.Timeout as exc:
            raise NetworkError(
                "The request to GitHub timed out. Check your connection and try again."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise NetworkError(
                "Could not connect to GitHub. Check your internet connection and try again."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise NetworkError(f"The request to GitHub failed: {exc}") from exc

        self._record_rate_limit(response)
        return response

    def _record_rate_limit(self, response: requests.Response) -> None:
        headers = getattr(response, "headers", None) or {}
        try:
            self.rate_limit_remaining = int(headers.get("X-RateLimit-Remaining"))
            self.rate_limit_total = int(headers.get("X-RateLimit-Limit"))
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _message(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return ""
        return str(body.get("message", "")) if isinstance(body, dict) else ""

    def _raise_for_status(self, response: requests.Response) -> None:
        status = response.status_code
        if status == 200:
            return

        message = self._message(response)
        headers = getattr(response, "headers", None) or {}

        is_rate_limited = status == 429 or (
            status == 403
            and (headers.get("X-RateLimit-Remaining") == "0"
                 or "rate limit" in message.lower())
        )
        if is_rate_limited:
            reset_text = ""
            try:
                reset_at = datetime.fromtimestamp(
                    int(headers.get("X-RateLimit-Reset")), tz=timezone.utc
                )
                reset_text = f" The limit resets at {reset_at:%H:%M} UTC."
            except (TypeError, ValueError):
                pass
            raise RateLimitError(
                "The GitHub API rate limit has been reached." + reset_text
                + " Add a personal access token in the sidebar to raise the limit."
            )
        if status == 401:
            raise GitHubAPIError(
                "GitHub rejected the access token. Remove it or enter a valid one."
            )
        if status == 404:
            raise RepositoryNotFoundError(
                "Repository not found. It may not exist, or it may be private."
            )
        if status == 451:
            raise RepositoryNotFoundError(
                "This repository is unavailable for legal reasons."
            )
        raise GitHubAPIError(
            f"GitHub returned an unexpected response (HTTP {status})."
            + (f" {message}" if message else "")
        )

    def _get_json(self, path: str):
        response = self._request(path)
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubAPIError("GitHub returned a response that could not be read.") from exc

    # -- public endpoints ---------------------------------------------------
    def get_repository(self, owner: str, repo: str) -> dict:
        return self._get_json(f"/repos/{owner}/{repo}")

    def get_languages(self, owner: str, repo: str) -> dict:
        data = self._get_json(f"/repos/{owner}/{repo}/languages")
        return data if isinstance(data, dict) else {}

    def list_directory(self, owner: str, repo: str, path: str = "") -> list[dict]:
        """List a directory. Returns ``[]`` if the path is missing or the repo is empty."""
        suffix = f"/{path}" if path else ""
        response = self._request(f"/repos/{owner}/{repo}/contents{suffix}")
        if response.status_code == 404:
            return []
        self._raise_for_status(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise GitHubAPIError("GitHub returned a response that could not be read.") from exc
        return data if isinstance(data, list) else []


# --------------------------------------------------------------------------- #
# Parsing API data
# --------------------------------------------------------------------------- #
def build_repository_info(data: dict) -> RepositoryInfo:
    owner = (data.get("owner") or {}).get("login", "")
    license_data = data.get("license") or {}
    return RepositoryInfo(
        owner=owner,
        name=data.get("name", ""),
        full_name=data.get("full_name", ""),
        html_url=data.get("html_url", ""),
        description=(data.get("description") or "").strip(),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        pushed_at=data.get("pushed_at") or "",
        stars=data.get("stargazers_count", 0),
        forks=data.get("forks_count", 0),
        open_issues=data.get("open_issues_count", 0),
        size_kb=data.get("size", 0),
        default_branch=data.get("default_branch", ""),
        license_name=license_data.get("name") or None,
        archived=bool(data.get("archived", False)),
        is_fork=bool(data.get("fork", False)),
    )


def calculate_language_stats(language_bytes: dict) -> list[LanguageStat]:
    """Convert GitHub's bytes-per-language mapping into sorted percentages."""
    total = sum(value for value in language_bytes.values() if value > 0)
    if total == 0:
        return []
    stats = [
        LanguageStat(name=name, bytes=value, percentage=round(value / total * 100, 2))
        for name, value in language_bytes.items() if value > 0
    ]
    return sorted(stats, key=lambda stat: stat.bytes, reverse=True)


# --------------------------------------------------------------------------- #
# Structure checks
# --------------------------------------------------------------------------- #
def _names(entries: list[dict], entry_type: str) -> list[str]:
    return [e.get("name", "") for e in entries if e.get("type") == entry_type]


def _first_match(names: list[str], predicate: Callable[[str], bool]) -> Optional[str]:
    return next((name for name in names if predicate(name.lower())), None)


def run_structure_checks(root_entries: list[dict], workflow_entries: list[dict],
                         license_name: Optional[str] = None) -> list[CheckResult]:
    """Run the six checklist items against the data fetched from the API."""
    files = _names(root_entries, "file")
    directories = _names(root_entries, "dir")

    readme = _first_match(files, lambda n: n.startswith("readme"))
    dependency = _first_match(
        files,
        lambda n: n in DEPENDENCY_FILE_NAMES
        or (n.startswith("requirements") and n.endswith(".txt"))
        or n.endswith(".csproj"),
    )
    test_dir = _first_match(directories, lambda n: n in TEST_DIRECTORY_NAMES)
    license_file = _first_match(
        files, lambda n: n.startswith(("license", "licence", "copying", "unlicense"))
    )
    gitignore = _first_match(files, lambda n: n == ".gitignore")
    workflow_files = [
        e.get("name", "") for e in workflow_entries
        if e.get("type") == "file" and e.get("name", "").lower().endswith((".yml", ".yaml"))
    ]

    license_evidence = license_file
    if not license_evidence and license_name:
        license_evidence = f"Detected by GitHub: {license_name}"

    return [
        CheckResult(
            "readme", "README file", readme is not None,
            readme or "No file starting with 'README' in the repository root.",
            "A README tells visitors what the project does and how to run it.",
            "Add a README.md with an overview, installation steps, usage examples and screenshots.",
        ),
        CheckResult(
            "dependencies", "Dependency or configuration file", dependency is not None,
            dependency or "No known dependency or build file in the repository root.",
            "A dependency file lets others install the exact packages the project needs.",
            "Add a manifest for your ecosystem, such as requirements.txt, pyproject.toml or package.json.",
        ),
        CheckResult(
            "tests", "Test directory", test_dir is not None,
            f"{test_dir}/" if test_dir else "No 'test', 'tests', 'spec' or '__tests__' directory in the repository root.",
            "A test directory shows the project has automated checks that protect against regressions.",
            "Create a tests/ directory and add unit tests with pytest, unittest, Jest or your language's framework.",
        ),
        CheckResult(
            "license", "License file", license_evidence is not None,
            license_evidence or "No LICENSE file found and GitHub did not detect a license.",
            "Without a license, others have no legal permission to use, copy or modify the code.",
            "Add a LICENSE file. The MIT License is a common choice; choosealicense.com can help you decide.",
        ),
        CheckResult(
            "gitignore", ".gitignore file", gitignore is not None,
            gitignore or "No .gitignore in the repository root.",
            "A .gitignore keeps virtual environments, caches, secrets and build output out of version control.",
            "Add a .gitignore for your language; GitHub offers ready-made templates when creating one.",
        ),
        CheckResult(
            "workflows", "GitHub Actions workflow", bool(workflow_files),
            ", ".join(f"{WORKFLOW_DIRECTORY}/{name}" for name in workflow_files)
            if workflow_files else f"No .yml or .yaml file in {WORKFLOW_DIRECTORY}/.",
            "A workflow can run tests and linting automatically on every push or pull request.",
            "Add a workflow under .github/workflows/ that installs dependencies and runs your tests.",
        ),
    ]


def build_summary(info: RepositoryInfo, checks: list[CheckResult],
                  languages: list[LanguageStat], is_empty: bool) -> str:
    """Write a plain-language structure summary from the actual check results."""
    if is_empty:
        return (
            f"{info.full_name} has no files in its root directory, so none of the "
            "structure checks could be satisfied."
        )
    found = [c.label for c in checks if c.found]
    missing = [c.label for c in checks if not c.found]
    parts = [f"{len(found)} of {len(checks)} recommended items were found in {info.full_name}."]
    if found:
        parts.append("Found: " + ", ".join(found) + ".")
    if missing:
        parts.append("Not found: " + ", ".join(missing) + ".")
    if languages:
        top = languages[0]
        parts.append(f"The main language reported by GitHub is {top.name} ({top.percentage:.1f}% of code bytes).")
    return " ".join(parts)


def build_suggestions(info: RepositoryInfo, checks: list[CheckResult],
                      languages: list[LanguageStat], is_empty: bool) -> list[str]:
    suggestions: list[str] = []
    if is_empty:
        suggestions.append(
            "Push an initial commit. Start with a README.md, a .gitignore and a LICENSE file."
        )
        return suggestions
    suggestions.extend(check.suggestion for check in checks if not check.found)
    if not info.description:
        suggestions.append(
            "Add a short repository description in the GitHub settings so the project is easier to find and understand."
        )
    if not languages:
        suggestions.append(
            "GitHub reports no language data. Check that the repository contains source code files."
        )
    if info.archived:
        suggestions.append(
            "This repository is archived and read-only. Unarchive it if you plan to keep developing it."
        )
    return suggestions


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def analyze_repository(url: str, token: Optional[str] = None,
                       client: Optional[GitHubClient] = None) -> AnalysisResult:
    """Validate ``url``, fetch data from GitHub and run all analyses."""
    owner, repo = parse_repository_url(url)
    client = client or GitHubClient(token=token)

    info = build_repository_info(client.get_repository(owner, repo))
    languages = calculate_language_stats(client.get_languages(info.owner or owner, info.name or repo))
    root_entries = client.list_directory(owner, repo)
    is_empty = len(root_entries) == 0

    workflow_entries: list[dict] = []
    if any(e.get("type") == "dir" and e.get("name") == ".github" for e in root_entries):
        workflow_entries = client.list_directory(owner, repo, WORKFLOW_DIRECTORY)

    checks = run_structure_checks(root_entries, workflow_entries, info.license_name)
    return AnalysisResult(
        repository=info,
        languages=languages,
        checks=checks,
        root_entries=[{"name": e.get("name", ""), "type": e.get("type", "")} for e in root_entries],
        is_empty=is_empty,
        suggestions=build_suggestions(info, checks, languages, is_empty),
        summary=build_summary(info, checks, languages, is_empty),
        analyzed_at=datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
    )


# --------------------------------------------------------------------------- #
# Markdown report
# --------------------------------------------------------------------------- #
def generate_markdown_report(result: AnalysisResult) -> str:
    info = result.repository
    lines = [
        f"# Repository analysis: {info.full_name}",
        "",
        f"Generated on {result.analyzed_at} from the GitHub REST API.",
        "",
        "## Repository details",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Name | {info.name} |",
        f"| Owner | {info.owner} |",
        f"| URL | {info.html_url} |",
        f"| Description | {info.description or 'No description provided'} |",
        f"| Created | {format_date(info.created_at)} |",
        f"| Last updated | {format_date(info.updated_at)} |",
        f"| Last push | {format_date(info.pushed_at)} |",
        f"| Default branch | {info.default_branch or 'Unknown'} |",
        f"| License (detected by GitHub) | {info.license_name or 'None detected'} |",
        f"| Stars | {info.stars:,} |",
        f"| Forks | {info.forks:,} |",
        f"| Open issues and pull requests | {info.open_issues:,} |",
        f"| Size | {format_size(info.size_kb)} |",
        "",
        "## Programming languages",
        "",
    ]
    if result.languages:
        lines += ["| Language | Bytes of code | Share |", "| --- | ---: | ---: |"]
        lines += [f"| {s.name} | {s.bytes:,} | {s.percentage:.2f}% |" for s in result.languages]
    else:
        lines.append("GitHub reported no language data for this repository.")

    lines += ["", "## Structure checks", "",
              "| Check | Result | Evidence |", "| --- | --- | --- |"]
    lines += [
        f"| {c.label} | {'Found' if c.found else 'Not found'} | {c.evidence} |"
        for c in result.checks
    ]
    lines += ["", "## Summary", "", result.summary, "", "## Suggestions", ""]
    if result.suggestions:
        lines += [f"- {s}" for s in result.suggestions]
    else:
        lines.append("All six recommended items were found. No structural improvements to suggest.")

    missing = result.missing_checks
    if missing and not result.is_empty:
        lines += ["", "## Why the missing items matter", ""]
        lines += [f"- **{c.label}**: {c.why_it_matters}" for c in missing]

    lines += [
        "", "## Methodology", "",
        "- Language shares are calculated from the bytes of code per language returned by GitHub's languages endpoint.",
        "- Structure checks look for well-known file and directory names in the repository root and in `.github/workflows`.",
        "- This report checks repository organization only. It does not measure code quality, correctness or test coverage.",
        "",
    ]
    return "\n".join(lines)
