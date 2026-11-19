"""Noxfile."""

import shutil
from pathlib import Path

import nox

nox.options.default_venv_backend = "none"
nox.options.sessions = ["lints"]


CLEANABLE_TARGETS = [
    "./dist",
    "./site",
    "./.cache",
    "./coverage.xml",
    "./**/__pycache__",
    "./**/*.pyc",
    "./**/*.pyo",
]


@nox.session
def tests(session: nox.Session) -> None:
    """Run tests."""
    session.run("python", "-m", "coverage", "run", "-m", "pytest", "-v")


@nox.session
def lints(session: nox.Session) -> None:
    """Run lints."""
    session.run("prek", "run", "--all-files")
    session.run("ruff", "format", ".")
    session.run("ruff", "check", "--fix", ".")
    session.run("mypy", "--strict", "src/", "tests/")
    session.run("ty", "check", ".")


@nox.session
def docs(session: nox.Session) -> None:
    """Build the documentation (needs the ``docs`` dependency group)."""
    session.run("sphinx-build", "--builder", "dirhtml", "--nitpicky", "docs", "site")


@nox.session
def clean(_: nox.Session) -> None:
    """Clean cache, .pyc, .pyo, and test/build artifact files from project."""
    count = 0
    for searchpath in CLEANABLE_TARGETS:
        for filepath in Path().glob(searchpath):
            if filepath.is_dir():
                shutil.rmtree(filepath)
            else:
                filepath.unlink()
            count += 1
