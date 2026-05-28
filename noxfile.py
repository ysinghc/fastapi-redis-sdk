"""Nox sessions — local mirror of CI checks.

Run all checks:         nox
Run one session:        nox -s lint
List sessions:          nox -l
Serve docs locally:     nox -s docs-serve

CI uses these same sessions so there is a single source of truth
for linting, type-checking, security scanning, and testing.
"""

from __future__ import annotations

import nox

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True

SOURCES = ["src/", "tests/"]
PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
PYTHON_DEFAULT = "3.12"


# ---------------------------------------------------------------------------
# Lint & format
# ---------------------------------------------------------------------------
@nox.session(python=PYTHON_DEFAULT)
def lint(session: nox.Session) -> None:
    """Run ruff linter and formatter checks."""
    session.install("ruff>=0.12.5")
    session.run("ruff", "check", *SOURCES)
    session.run("ruff", "format", "--check", *SOURCES)


# ---------------------------------------------------------------------------
# Type checking
# ---------------------------------------------------------------------------
@nox.session(python=PYTHON_DEFAULT)
def typecheck(session: nox.Session) -> None:
    """Run mypy on source code."""
    session.install(
        ".[otel]",
        "mypy>=1.17.0",
        "pydantic>=2.12.5",
        "pydantic-settings>=2.0.0",
    )
    session.run("mypy", "src/")


# ---------------------------------------------------------------------------
# Security scan
# ---------------------------------------------------------------------------
@nox.session(python=PYTHON_DEFAULT)
def security(session: nox.Session) -> None:
    """Run bandit security scanner."""
    session.install("bandit[toml]>=1.8.6")
    # JSON report for CI artifact upload (non-fatal); text report (fatal).
    session.run(
        "bandit", "-r", "src/",
        "-f", "json", "-o", "bandit-report.json",
        success_codes=[0, 1],
    )
    session.run("bandit", "-r", "src/")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    """Run the full test suite with coverage."""
    session.install("-e", ".[otel]")
    session.install(
        "pytest>=9.0.0",
        "pytest-asyncio>=1.1.0",
        "pytest-cov>=6.2.1",
        "pytest-mock>=3.12.0",
        "pytest-timeout>=2.4.0",
        "fakeredis>=2.35.0",
        "httpx>=0.23.0,<1.0.0",
        "anyio[trio]>=3.2.1,<5.0.0",
        "coverage[toml]>=7.10.1",
    )
    session.run("pytest", "tests/", "-v", *session.posargs)


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------
DOCS_DEPS = [
    "mkdocs>=1.5.0",
    "mkdocs-material>=9.5.0",
    "mkdocs-git-revision-date-localized-plugin>=1.2.0",
]


@nox.session(python=PYTHON_DEFAULT, default=False)
def docs_build(session: nox.Session) -> None:
    """Build the documentation site.  Pass ``-- --strict`` for CI."""
    session.install(*DOCS_DEPS)
    session.run("mkdocs", "build", *session.posargs)


@nox.session(python=PYTHON_DEFAULT, default=False)
def docs_serve(session: nox.Session) -> None:
    """Serve the documentation site locally with live reload."""
    session.install(*DOCS_DEPS)
    session.run("mkdocs", "serve", *session.posargs)


# ---------------------------------------------------------------------------
# Auto-fix helpers (not run by default or CI)
# ---------------------------------------------------------------------------
@nox.session(python=PYTHON_DEFAULT, default=False)
def fix(session: nox.Session) -> None:
    """Auto-fix lint and format issues."""
    session.install("ruff>=0.12.5")
    session.run("ruff", "check", "--fix", *SOURCES)
    session.run("ruff", "format", *SOURCES)
