"""Thin command entry points; later phases supply the remaining workflows."""

from pathlib import Path
import subprocess

import typer

from phantom import lockfile, render
from phantom.bisect import BoundaryError, InconclusiveError, run_bisect
from phantom.diagnose import diagnose
from phantom.doctor import FAIL, run_doctor
from phantom.exorcise import DirtyReadmeError, add_copilot, apply as exorcise_apply, plan as exorcise_plan
from phantom.lockfile import LockError
from phantom.models import Finding, RunResult, Status
from phantom.parse import committed_readme, extract_steps_from_text
from phantom.risk import flag_steps
from phantom.rules import classify
from phantom.runner import resolve_sha, run_steps

app = typer.Typer(help="Find the hidden assumptions haunting repository setup.")
AI_DISABLED = "AI disabled with --no-ai"


HauntOutcome = tuple[RunResult, Finding | None, str | None, list[str]]


def _haunt(path: Path, rev: str, use_ai: bool) -> HauntOutcome:
    repo = path.resolve()
    sha = resolve_sha(repo, rev)
    # Parse the same committed documentation as the source copied into Docker.
    _, readme = committed_readme(repo, sha)
    steps = extract_steps_from_text(readme)
    if not steps:
        raise ValueError("No runnable fenced shell setup steps found in the committed README.")
    flags = flag_steps(steps)
    result = run_steps(repo, sha, steps)
    failure = result.first_failure
    if failure is None or result.infra_error:
        return result, None, None, flags
    finding = classify(failure, repo, sha)
    note = AI_DISABLED
    if use_ai:
        finding, note = diagnose(finding, steps, repo, sha)
    finding.risk_flags = flags
    return result, finding, note, flags


@app.command()
def haunt(
    path: Path = typer.Argument(Path("."), help="Repository containing README.md."),
    rev: str = typer.Option("HEAD", help="Committed revision to check."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Use deterministic rules only."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Run committed README setup in a fresh container and name the first failure."""
    try:
        with render.running(json_output):
            result, finding, ai_note, flags = _haunt(path, rev, not no_ai)
    except subprocess.CalledProcessError:
        render.error("Cannot read the Git revision or committed README. Check the path and --rev.", json_output)
        raise typer.Exit(2)
    except subprocess.TimeoutExpired:
        render.error("Reading the repository timed out. Check the repository and retry.", json_output)
        raise typer.Exit(2)
    except (OSError, ValueError) as error:
        render.error(str(error), json_output)
        raise typer.Exit(2)
    render.haunt_result(result, finding, ai_note, json_output, flags)
    if result.infra_error or (finding and finding.status == Status.INCONCLUSIVE):
        raise typer.Exit(2)
    if result.passed:
        raise typer.Exit(0)
    raise typer.Exit(1 if finding else 2)


@app.command()
def bisect(
    path: Path = typer.Argument(Path("."), help="Repository containing README.md."),
    good: str | None = typer.Option(None, help="Known-good commit (default: oldest first-parent commit)."),
    bad: str = typer.Option("HEAD", help="Known-bad commit."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Skip the final AI explanation."),
) -> None:
    """Find the first commit whose code breaks today's README setup."""
    try:
        with render.running(False, "Testing commits in fresh containers…"):
            result = run_bisect(path, good, bad, use_ai=not no_ai, on_probe=render.bisect_probe)
    except (BoundaryError, subprocess.CalledProcessError) as error:
        message = str(error) if isinstance(error, BoundaryError) else (
            "Cannot read the Git history. Check the path, --good and --bad.")
        render.error(message)
        raise typer.Exit(1)
    except subprocess.TimeoutExpired:
        render.error("Reading the repository timed out. Check the repository and retry.")
        raise typer.Exit(2)
    except (InconclusiveError, OSError, ValueError) as error:
        render.error(str(error))
        raise typer.Exit(2)
    render.bisect_result(result, not no_ai)
    raise typer.Exit(0 if result.culprit else 2)


@app.command()
def exorcise(
    path: Path = typer.Argument(Path("."), help="Repository containing README.md."),
    rev: str = typer.Option("HEAD", help="Committed revision to verify against."),
    apply: bool = typer.Option(False, "--apply", help="Write changes without asking."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Use deterministic rules only."),
    copilot: bool = typer.Option(False, "--copilot",
                                 help="Also write .github/workflows/copilot-setup-steps.yml."),
) -> None:
    """Verify a README-only fix in fresh containers, then write README.md and assumptions.lock."""
    try:
        with render.running(False, "Verifying README fixes in fresh containers…"):
            exorcism = exorcise_plan(path, rev, use_ai=not no_ai)
        if copilot:
            exorcism = add_copilot(exorcism)
    except subprocess.CalledProcessError:
        render.error("Cannot read the Git revision or committed README. Check the path and --rev.")
        raise typer.Exit(2)
    except subprocess.TimeoutExpired:
        render.error("Reading the repository timed out. Check the repository and retry.")
        raise typer.Exit(2)
    except (DirtyReadmeError, LockError, OSError, ValueError) as error:
        render.error(str(error))
        raise typer.Exit(2)
    render.exorcise_plan(exorcism)
    verification = exorcism.verification
    if verification.inconclusive:
        raise typer.Exit(2)
    if verification.nothing_to_fix:
        raise typer.Exit(0)
    if not verification.verified:
        raise typer.Exit(1)
    if not apply and not typer.confirm("Apply these changes?", default=False):
        render.info("No files changed.")
        raise typer.Exit(0)
    try:
        written = exorcise_apply(exorcism)
    except (DirtyReadmeError, OSError) as error:
        render.error(str(error))
        raise typer.Exit(2)
    render.exorcise_written(written)


@app.command()
def doctor(
    lock: Path = typer.Option(Path(lockfile.LOCK_NAME), "--lock", help="Lock file to check against."),
) -> None:
    """Check this machine against assumptions.lock (no Docker, no AI)."""
    try:
        results = run_doctor(lock)
    except (LockError, OSError) as error:
        render.error(str(error))
        raise typer.Exit(2)
    render.doctor_results(results)
    raise typer.Exit(1 if any(result.state == FAIL for result in results) else 0)
