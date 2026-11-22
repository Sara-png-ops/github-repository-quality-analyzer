# GitHub Repository Quality Analyzer

A Streamlit dashboard that analyzes any public GitHub repository using the GitHub REST API. It reports repository details, programming language distribution and the presence of key project files, then explains what is missing and exports the findings as a Markdown report.

All data comes from live API responses. Nothing is hardcoded or simulated.

## Features

- Validates GitHub repository URLs (supports `.git` suffixes, trailing slashes, `/tree/...` paths and `www.`).
- Shows name, description, owner, creation date, last updated date, stars, forks, open issues and size.
- Shows language percentages with a bar chart and a data table.
- Checks for six recommended items and marks each as Found or Not found, with the evidence used:
  - README file
  - Dependency or configuration file (`requirements.txt`, `pyproject.toml`, `package.json`, `pom.xml`, `Cargo.toml`, `go.mod` and others)
  - Test directory (`test`, `tests`, `spec`, `__tests__`)
  - License file
  - `.gitignore` file
  - GitHub Actions workflow (`.yml` or `.yaml` in `.github/workflows`)
- Explains why each missing item matters and suggests how to fix it.
- Downloads a Markdown report.
- Handles invalid URLs, private or missing repositories, rate limits, network failures, empty repositories and missing descriptions.
- Optional GitHub token field in the sidebar to raise the rate limit.

## Methodology

The analyzer is deliberately transparent and does not compute a quality score.

- **Language share** = bytes of code for a language divided by the total bytes returned by GitHub's `/languages` endpoint.
- **Structure checks** = a check is Found only if a matching file or directory name is returned by the API for the repository root (or `.github/workflows`).
- **Summary** = "N of 6 recommended items found" plus the names of what was found and what was not.

The tool checks repository organization. It does not read source code, measure code quality or measure test coverage.

## Technology stack

- Python 3.10 or newer
- Streamlit (dashboard)
- Requests (GitHub REST API)
- Pandas and Altair (tables and chart)
- Pytest (unit tests)

## Project structure

```
github-repository-quality-analyzer/
├── app.py                 Streamlit dashboard
├── github_analyzer.py     API client, analysis logic, report generation
├── requirements.txt
├── README.md
├── .gitignore
├── .streamlit/
│   └── config.toml        Lavender theme settings
└── tests/
    └── test_analyzer.py   Unit tests with mocked API responses
```

## Installation

```bash
git clone https://github.com/<your-username>/github-repository-quality-analyzer.git
cd github-repository-quality-analyzer

python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (Command Prompt)
.venv\Scripts\activate.bat
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

If PowerShell blocks the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

## VS Code setup

1. Install [Python](https://www.python.org/downloads/) 3.10+ and [VS Code](https://code.visualstudio.com/).
2. Install the **Python** extension by Microsoft.
3. Select **File > Open Folder** and open `github-repository-quality-analyzer`.
4. Open the integrated terminal with **Terminal > New Terminal** and create and activate the virtual environment as shown above.
5. Press `Ctrl+Shift+P` (`Cmd+Shift+P` on macOS), choose **Python: Select Interpreter** and pick the one inside `.venv`.
6. Run `pip install -r requirements.txt` in the terminal.

## Running the app

```bash
streamlit run app.py
```

Streamlit prints a local address, normally `http://localhost:8501`, and opens it in your browser. Paste a public repository URL such as `https://github.com/psf/requests` and select **Analyze**.

### Rate limits and tokens

Unauthenticated GitHub API requests are limited to 60 per hour, and each analysis uses 3 to 4 requests. If you reach the limit, create a [personal access token](https://github.com/settings/tokens) (no scopes are needed for public repositories) and paste it into the sidebar. The token is sent only to `api.github.com` and is not stored. Results are cached for five minutes to save requests.

## Running the tests

```bash
python -m pytest -v
```

The tests mock all GitHub responses, so they need no internet connection or token. They cover URL validation, API response handling, rate limits, network errors, empty repositories, missing descriptions, structure checks and report generation.

## Screenshots

Run the app, analyze a repository, then save screenshots into a `docs/screenshots/` folder and reference them here:

```markdown
![Dashboard overview](docs/screenshots/overview.png)
![Language chart](docs/screenshots/languages.png)
![Structure checks](docs/screenshots/checks.png)
```

## Limitations

- Checks look only at the repository root and `.github/workflows`. A test folder at `src/tests` or a `requirements.txt` inside a subfolder of a monorepo is not detected.
- Detection is based on file and directory names. It does not confirm that a README is useful or that tests pass.
- Language data comes from GitHub's linguist classification, which may exclude vendored or generated files.
- GitHub includes open pull requests in the open issues count.
- Only public repositories are supported.
- Unauthenticated use is limited to 60 API requests per hour.

## Future improvements

- Check common subdirectories such as `src/` for tests and dependency files.
- Add commit activity and contributor statistics.
- Compare two repositories side by side.
- Export reports as PDF.
- Add continuous integration that runs the test suite on every push.

## License

Add a `LICENSE` file before publishing (the MIT License is a common choice for portfolio projects).
