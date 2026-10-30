"""Thin command entry points; later phases supply the remaining workflows."""

from pathlib import Path
import subprocess

import typer

from ghostdev import config, render
from ghostdev.models import Finding, RunResult, Status
from ghostdev.parse import extract_steps_from_text, find_readme
from ghostdev.rules import classify
from ghostdev.runner import resolve_sha, run_steps

app = typer.Typer(help="Find the hidden assumptions haunting repository setup.")


def _haunt(path: Path, rev: str) -> tuple[RunResult, Finding | None]:
    repo = path.resolve()
    sha = resolve_sha(repo, rev)
    readme = find_readme(repo)
    # Parse the same committed documentation as the source copied into Docker.
    source = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{readme.name}"],
                            capture_output=True, text=True, check=True, timeout=config.GIT_TIMEOUT)
    steps = extract_steps_from_text(source.stdout)
    if not steps:
        raise ValueError("No runnable fenced shell setup steps found in the committed README.")
    result = run_steps(repo, sha, steps)
    finding = classify(result.first_failure, repo, sha) if result.first_failure and not result.infra_error else None
    return result, finding


@app.command()
def haunt(
    path: Path = typer.Argument(Path("."), help="Repository containing README.md."),
    rev: str = typer.Option("HEAD", help="Committed revision to check."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Use deterministic rules only."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Run committed README setup in a fresh container and name the first failure."""
    # NOTE: Phase one is rules-only; --no-ai keeps the interface ready for Gemma.
    try:
        with render.running(json_output):
            result, finding = _haunt(path, rev)
    except subprocess.CalledProcessError:
        render.error("Cannot read the Git revision or committed README. Check the path and --rev.", json_output)
        raise typer.Exit(2)
    except subprocess.TimeoutExpired:
        render.error("Reading the repository timed out. Check the repository and retry.", json_output)
        raise typer.Exit(2)
    except (OSError, ValueError) as error:
        render.error(str(error), json_output)
        raise typer.Exit(2)
    render.haunt_result(result, finding, json_output)
    if result.infra_error or (finding and finding.status == Status.INCONCLUSIVE):
        raise typer.Exit(2)
    if result.passed:
        raise typer.Exit(0)
    raise typer.Exit(1 if finding else 2)


@app.command()
def bisect() -> None:
    """Find the first broken commit (planned for phase two)."""
    render.not_implemented()


@app.command()
def exorcise() -> None:
    """Verify and apply documentation fixes (planned for phase two)."""
    render.not_implemented()


@app.command()
def doctor() -> None:
    """Check local requirements (planned for phase two)."""
    render.not_implemented()
