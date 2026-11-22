"""Streamlit dashboard for the GitHub Repository Quality Analyzer."""

from __future__ import annotations

from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from github_analyzer import (
    AnalysisResult,
    GitHubAPIError,
    GitHubClient,
    InvalidURLError,
    NetworkError,
    RateLimitError,
    RepositoryNotFoundError,
    analyze_repository,
    format_date,
    format_size,
    generate_markdown_report,
)

PURPLE = "#6B4FBB"
MAX_CHART_BARS = 10

st.set_page_config(
    page_title="GitHub Repository Quality Analyzer",
    page_icon=":material/analytics:",
    layout="wide",
)

STYLES = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&display=swap');
html, body, .stApp, [data-testid="stMarkdownContainer"], button, input {
    font-family: 'DM Sans', -apple-system, 'Segoe UI', Roboto, sans-serif;
}
.block-container { padding-top: 2.2rem; max-width: 1150px; }
h1, h2, h3 { color: #2A2440; letter-spacing: -0.01em; }
.page-title { font-size: 2rem; font-weight: 700; margin: 0 0 .25rem 0; color: #2A2440; }
.page-subtitle { color: #6A6285; margin: 0 0 1.4rem 0; font-size: 1.02rem; max-width: 46rem; }
.section-title { font-size: 1.25rem; font-weight: 700; margin: 2.2rem 0 .8rem 0; color: #2A2440; }
.section-note { color: #6A6285; font-size: .92rem; margin: -.4rem 0 .9rem 0; }
.card {
    background: #FFFFFF; border: 1px solid #E3DAF5; border-radius: 12px;
    padding: 1.1rem 1.25rem; height: 100%;
}
.repo-card h2 { margin: 0 0 .3rem 0; font-size: 1.5rem; }
.repo-card a { color: #5B3FA8; text-decoration: none; }
.repo-card a:hover { text-decoration: underline; }
.repo-desc { color: #4A4264; margin: 0 0 .9rem 0; }
.repo-desc.empty { color: #8A83A3; font-style: italic; }
.meta-row { display: flex; flex-wrap: wrap; gap: .5rem 2rem; color: #4A4264; font-size: .93rem; }
.meta-row span b { color: #2A2440; font-weight: 500; }
.metric-label { color: #6A6285; font-size: .9rem; margin-bottom: .2rem; }
.metric-value { color: #3B2A74; font-size: 1.9rem; font-weight: 700; line-height: 1.15; }
.check-title { font-weight: 700; color: #2A2440; margin: .55rem 0 .3rem 0; }
.check-evidence { color: #4A4264; font-size: .9rem; word-break: break-word; }
.badge { display: inline-block; padding: .15rem .65rem; border-radius: 999px;
         font-size: .82rem; font-weight: 700; }
.badge.found { background: #E4F3EA; color: #1F6B45; }
.badge.missing { background: #FBE6E8; color: #9B2F3B; }
.summary-box { background: #FFFFFF; border-left: 4px solid #6B4FBB; border-radius: 6px;
               padding: .9rem 1.1rem; color: #2A2440; border-top: 1px solid #E3DAF5;
               border-right: 1px solid #E3DAF5; border-bottom: 1px solid #E3DAF5; }
[data-testid="stSidebar"] { border-right: 1px solid #E3DAF5; }
[data-testid="stFormSubmitButton"] button, [data-testid="stDownloadButton"] button {
    background: #6B4FBB; color: #FFFFFF; border: 0; border-radius: 8px; font-weight: 500;
}
[data-testid="stFormSubmitButton"] button:hover, [data-testid="stDownloadButton"] button:hover {
    background: #5A3FA6; color: #FFFFFF;
}
[data-testid="stForm"] { background: #FFFFFF; border: 1px solid #E3DAF5; border-radius: 12px; }
</style>
"""


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=300, show_spinner=False)
def run_analysis(url: str, token: str) -> tuple[AnalysisResult, int | None, int | None]:
    """Run the analysis and cache it for five minutes to save API requests."""
    client = GitHubClient(token=token or None)
    result = analyze_repository(url, client=client)
    return result, client.rate_limit_remaining, client.rate_limit_total


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def render_section(title: str, note: str = "") -> None:
    st.markdown(f'<div class="section-title">{escape(title)}</div>', unsafe_allow_html=True)
    if note:
        st.markdown(f'<div class="section-note">{escape(note)}</div>', unsafe_allow_html=True)


def metric_card(label: str, value: str) -> str:
    return (
        f'<div class="card"><div class="metric-label">{escape(label)}</div>'
        f'<div class="metric-value">{escape(value)}</div></div>'
    )


def render_repository_header(result: AnalysisResult) -> None:
    info = result.repository
    description = (
        f'<p class="repo-desc">{escape(info.description)}</p>'
        if info.description
        else '<p class="repo-desc empty">No description provided for this repository.</p>'
    )
    st.markdown(
        f"""
        <div class="card repo-card">
            <h2><a href="{escape(info.html_url)}" target="_blank" rel="noopener">{escape(info.full_name)}</a></h2>
            {description}
            <div class="meta-row">
                <span>Owner: <b>{escape(info.owner)}</b></span>
                <span>Created: <b>{format_date(info.created_at)}</b></span>
                <span>Last updated: <b>{format_date(info.updated_at)}</b></span>
                <span>Default branch: <b>{escape(info.default_branch or "Unknown")}</b></span>
                <span>License: <b>{escape(info.license_name or "None detected")}</b></span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metrics(result: AnalysisResult) -> None:
    info = result.repository
    st.write("")
    columns = st.columns(4)
    cards = [
        ("Stars", f"{info.stars:,}"),
        ("Forks", f"{info.forks:,}"),
        ("Open issues", f"{info.open_issues:,}"),
        ("Repository size", format_size(info.size_kb)),
    ]
    for column, (label, value) in zip(columns, cards):
        column.markdown(metric_card(label, value), unsafe_allow_html=True)
    st.caption("GitHub counts open pull requests in the open issues figure. Size is the size GitHub reports for the repository.")


def render_languages(result: AnalysisResult) -> None:
    render_section(
        "Programming languages",
        "Share of code by bytes, as reported by GitHub's languages endpoint.",
    )
    if not result.languages:
        st.info("GitHub reported no language data for this repository.")
        return

    table = pd.DataFrame(
        {
            "Language": [s.name for s in result.languages],
            "Bytes": [s.bytes for s in result.languages],
            "Share (%)": [s.percentage for s in result.languages],
        }
    )

    chart_data = table[["Language", "Share (%)"]].copy()
    if len(chart_data) > MAX_CHART_BARS:
        kept = chart_data.iloc[: MAX_CHART_BARS - 1]
        rest = chart_data.iloc[MAX_CHART_BARS - 1:]
        other = pd.DataFrame(
            {"Language": [f"Other ({len(rest)} languages)"],
             "Share (%)": [round(rest["Share (%)"].sum(), 2)]}
        )
        chart_data = pd.concat([kept, other], ignore_index=True)

    base = alt.Chart(chart_data).encode(
        y=alt.Y("Language:N", sort=None, title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X("Share (%):Q", title="Share of code (%)", scale=alt.Scale(domain=[0, 100])),
        tooltip=["Language", alt.Tooltip("Share (%):Q", format=".2f")],
    )
    bars = base.mark_bar(color=PURPLE, cornerRadiusEnd=3, size=18)
    labels = base.mark_text(align="left", dx=5, color="#2A2440").encode(
        text=alt.Text("Share (%):Q", format=".1f")
    )
    chart = (bars + labels).properties(height=max(120, 32 * len(chart_data) + 30))

    chart_col, table_col = st.columns([3, 2])
    with chart_col:
        st.altair_chart(chart)
    with table_col:
        st.dataframe(table, hide_index=True)


def render_checks(result: AnalysisResult) -> None:
    render_section(
        "Repository structure",
        "Each check looks for well-known file or directory names in the repository root "
        "(and in .github/workflows for GitHub Actions).",
    )
    for start in range(0, len(result.checks), 3):
        columns = st.columns(3)
        for column, check in zip(columns, result.checks[start:start + 3]):
            badge = (
                '<span class="badge found">Found</span>'
                if check.found
                else '<span class="badge missing">Not found</span>'
            )
            column.markdown(
                f'<div class="card">{badge}<div class="check-title">{escape(check.label)}</div>'
                f'<div class="check-evidence">{escape(check.evidence)}</div></div>',
                unsafe_allow_html=True,
            )
        st.write("")


def render_insights(result: AnalysisResult) -> None:
    render_section("Quality insights")
    st.markdown(f'<div class="summary-box">{escape(result.summary)}</div>', unsafe_allow_html=True)
    st.caption(
        "This analysis covers repository organization only. It does not measure code quality, "
        "correctness or test coverage."
    )

    if result.is_empty:
        st.warning("The repository has no files in its root directory, so there is nothing to check yet.")

    missing = result.missing_checks
    if missing and not result.is_empty:
        st.markdown("**Why the missing items matter**")
        for check in missing:
            st.markdown(f"- **{check.label}:** {check.why_it_matters}")

    st.markdown("**Suggestions**")
    if result.suggestions:
        for suggestion in result.suggestions:
            st.markdown(f"- {suggestion}")
    else:
        st.success("All six recommended items were found. There are no structural improvements to suggest.")

    if result.root_entries:
        with st.expander(f"Root directory contents ({len(result.root_entries)} entries)"):
            folders = sorted(e["name"] for e in result.root_entries if e["type"] == "dir")
            files = sorted(e["name"] for e in result.root_entries if e["type"] != "dir")
            st.markdown("**Folders:** " + (", ".join(f"`{n}`" for n in folders) or "none"))
            st.markdown("**Files:** " + (", ".join(f"`{n}`" for n in files) or "none"))


def render_report_download(result: AnalysisResult) -> None:
    render_section("Download report")
    st.download_button(
        "Download Markdown report",
        data=generate_markdown_report(result),
        file_name=f"{result.repository.name}-analysis.md",
        mime="text/markdown",
    )


def render_error(exc: Exception) -> None:
    if isinstance(exc, InvalidURLError):
        st.error(f"Invalid URL. {exc}")
    elif isinstance(exc, RepositoryNotFoundError):
        st.error(f"{exc} Check the spelling and make sure the repository is public.")
    elif isinstance(exc, RateLimitError):
        st.warning(str(exc))
    elif isinstance(exc, NetworkError):
        st.error(f"Connection problem. {exc}")
    elif isinstance(exc, GitHubAPIError):
        st.error(str(exc))
    else:
        st.error(f"Something unexpected went wrong: {exc}")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def main() -> None:
    st.markdown(STYLES, unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("### Settings")
        token = st.text_input(
            "GitHub token (optional)",
            type="password",
            help="Anonymous requests are limited to 60 per hour. A token raises the limit to 5,000. "
                 "The token is only sent to api.github.com and is not stored.",
        )
        st.markdown("### How it works")
        st.markdown(
            "- Reads public data from the GitHub REST API.\n"
            "- Checks six recommended items in the repository root.\n"
            "- Reports what was found. It does not score code quality."
        )
        if "rate_limit" in st.session_state:
            remaining, total = st.session_state["rate_limit"]
            if remaining is not None and total is not None:
                st.caption(f"GitHub API requests remaining: {remaining} of {total}")

    st.markdown('<div class="page-title">GitHub Repository Quality Analyzer</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-subtitle">Enter a public repository URL to review its languages, '
        "structure and documentation, and download the findings as a report.</div>",
        unsafe_allow_html=True,
    )

    with st.form("analyze_form"):
        url_col, button_col = st.columns([5, 1], vertical_alignment="bottom")
        url = url_col.text_input(
            "Repository URL",
            placeholder="https://github.com/owner/repository",
        )
        submitted = button_col.form_submit_button("Analyze")

    if submitted:
        st.session_state.pop("result", None)
        st.session_state.pop("error", None)
        with st.spinner("Fetching data from GitHub..."):
            try:
                result, remaining, total = run_analysis(url, token)
                st.session_state["result"] = result
                st.session_state["rate_limit"] = (remaining, total)
            except Exception as exc:  # shown to the user in render_error
                st.session_state["error"] = exc
        st.rerun()

    if "error" in st.session_state:
        render_error(st.session_state["error"])
        return

    result = st.session_state.get("result")
    if result is None:
        st.info("Paste a repository URL above and select Analyze to begin.")
        return

    st.success(f"Analysis complete for {result.repository.full_name}.")
    render_repository_header(result)
    render_metrics(result)
    render_languages(result)
    render_checks(result)
    render_insights(result)
    render_report_download(result)


main()
