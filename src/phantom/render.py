"""All terminal presentation, including machine-readable output."""

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict
import json
from pathlib import Path
import shlex
from typing import Any, ContextManager

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from phantom import config
from phantom.bisect import BisectResult, FAIL, PASS
from phantom.doctor import CheckResult, FAIL, PASS as CHECK_PASS, WARN
from phantom.exorcise import ExorcisePlan
from phantom.lockfile import requirement_id
from phantom.models import Finding, ReqType, RunResult, Status, Step, StepResult
from phantom.verify import PatchError, render_fix

console = Console()
PANEL_WIDTH = 100
STATUS_STYLES = {
    Status.OBSERVED: "red", Status.INCONCLUSIVE: "magenta", Status.HYPOTHESIS: "yellow",
    Status.UNSUPPORTED: "dim", Status.VERIFIED_FIX: "green", Status.VERIFIED_TOGETHER: "green",
}


def running(json_output: bool,
            message: str = "Running README setup in a fresh container…") -> ContextManager[Any]:
    return nullcontext() if json_output else console.status(message)


def step_progress(status: Any) -> Callable[[Step, int], None] | None:
    """Update a live status line as each README step starts; None when nothing is shown."""
    if status is None:
        return None

    def update(step: Step, total: int) -> None:
        status.update(Text(f"Running step {step.index}/{total} · {step.command}", overflow="ellipsis"))
    return update


def _panel(body: Any, title: str) -> Panel:
    return Panel(body, title=title, title_align="left", width=min(console.width, PANEL_WIDTH))


def header(command: str, *parts: str) -> None:
    line = Text(f"{config.APP_NAME} {command}", style="bold")
    for part in parts:
        line.append(" · ", style="dim").append(part, style="dim")
    console.print(line)


def hint(message: str) -> None:
    # soft_wrap leaves long paths unbroken, so the command stays copyable.
    console.print(Text(f"→ {message}", style="cyan"), soft_wrap=True)


def _target(path: Path) -> str:
    """The path as the user would retype it, quoted for the shell."""
    return shlex.quote(str(path))


def _step_line(step: StepResult) -> Text:
    passed = step.exit_code == 0 and not step.timed_out
    line = Text(f"{'✅' if passed else '❌'} step {step.step.index}  {step.step.command}")
    if step.started:
        line.append(f"  (started, still running after {config.SERVER_GRACE}s)", style="dim")
    if step.duration is not None:
        line.append(f"  {step.duration:.1f}s", style="dim")
    return line


def error(message: str, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps({"status": "inconclusive", "error": message}))
    else:
        console.print(Text(message, style="red"), soft_wrap=True)


def suggested_fix(finding: Finding) -> str | None:
    if finding.fix is None:
        return None
    try:
        return render_fix(finding.fix)
    except PatchError:
        return None


def model_label(model: str | None) -> str:
    """Name the model honestly: the fallback model is not Gemma."""
    model = model or ""
    return "Gemma explanation" if model.startswith("gemma") else f"AI explanation ({model or 'model'})"


def explanation_label(ai_note: str | None) -> str:
    return model_label((ai_note or "").removeprefix("explained by "))


def risk_flags(flags: list[str]) -> None:
    if not flags:
        return
    console.print(Text("Risk flags (warnings only, nothing was blocked):", style="yellow"))
    for flag in flags:
        console.print(Text(f"⚠️  {flag}"))


def haunt_result(result: RunResult, finding: Finding | None, ai_note: str | None = None,
                 json_output: bool = False, flags: list[str] | None = None,
                 target: Path = Path(".")) -> None:
    flags = flags or []
    if json_output:
        print(json.dumps({"run": asdict(result), "finding": finding.to_dict() if finding else None,
                          "ai_note": ai_note, "risk_flags": flags}))
        return
    header("haunt", target.resolve().name, f"@ {result.sha[:7]}", config.IMAGE, result.platform)
    for step in result.steps:
        console.print(_step_line(step))
    risk_flags(flags)
    if result.infra_error:
        error(result.infra_error)
        return
    if result.passed:
        console.print("README setup passed.", style="green")
        return
    if finding is None:
        error("Setup ended before all commands completed. Retry in a fresh container.")
        return
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    details.add_row("Status", Text(finding.status.value, style=STATUS_STYLES.get(finding.status, "")))
    details.add_row("Type / Name", Text(f"{finding.req_type.value} / {finding.name or 'unknown'}"))
    details.add_row("Evidence", Text(finding.evidence or "No output captured.", style="red"))
    details.add_row("Cause", Text(finding.cause.value))
    if finding.explanation:
        details.add_row(explanation_label(ai_note), Text(finding.explanation))
    else:
        details.add_row("Explanation", Text(ai_note or "AI explanation unavailable"))
    command = suggested_fix(finding)
    if command:
        fix = Text(command, style="bold cyan").append("  not yet verified", style="dim")
        details.add_row("Suggested fix", fix)
    console.print(_panel(details, "Setup finding"))
    if finding.status == Status.INCONCLUSIVE:
        hint("Re-run in a fresh container; this result proves nothing either way.")
    elif command:
        hint(f"Verify and patch it: phantom exorcise {_target(target)}")
    elif finding.req_type == ReqType.TOOL:
        tool = finding.name or "the tool"
        hint(f"{tool} is missing from the test environment. Check the documented prerequisites "
             f"or set PHANTOM_IMAGE to an image that includes it.")
    elif finding.req_type in (ReqType.FILE, ReqType.MODULE) and finding.name:
        hint(f"{finding.name} was unavailable during setup. Check the documented prerequisites "
             f"and path; if it used to work, phantom bisect {_target(target)} finds when it broke.")
    else:
        hint(f"Find the commit that introduced it: phantom bisect {_target(target)}")


def bisect_probe(sha: str, verdict: str, cached: bool) -> None:
    icon = {PASS: "✅", FAIL: "❌"}.get(verdict, "⚠️ ")
    suffix = " (cached)" if cached else ""
    label = "skipped: infrastructure" if verdict not in (PASS, FAIL) else verdict
    console.print(Text(f"{icon} {sha[:7]}  {label}{suffix}"))


def bisect_result(result: BisectResult, use_ai: bool = True, target: Path = Path(".")) -> None:
    if result.cache_note:
        console.print(Text(result.cache_note, style="yellow"))
    finding = result.finding
    requirement = f"{finding.req_type.value} / {finding.name or 'unknown'}" if finding else "unknown"
    if result.culprit is None:
        suspects = ", ".join(sha[:7] for sha in result.suspects)
        error(f"Skipped commits hide the culprit. It is one of: {suspects}.")
        return
    commit = result.commit
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    details.add_row("Commit", Text(commit.sha[:7] if commit else result.culprit[:7]))
    if commit:
        details.add_row("Author", Text(commit.author))
        details.add_row("Date", Text(commit.date))
        details.add_row("Subject", Text(commit.subject))
    details.add_row("Requirement", Text(requirement))
    if finding:
        details.add_row("Evidence", Text(finding.evidence))
    details.add_row(model_label(result.explanation_model) if result.explanation else "Explanation",
                    Text(result.explanation or ("AI explanation unavailable or rejected" if use_ai
                                                else "AI disabled with --no-ai")))
    console.print(_panel(details, "First bad commit"))
    # Diffs sit outside the panel so long lines keep their colour and stay copyable.
    if commit and commit.stat:
        console.print(Text(commit.stat.strip("\n"), style="dim"))
    if commit and commit.hunk:
        console.print(Syntax(commit.hunk, "diff", theme="ansi_dark", background_color="default"))
    hint(f"phantom exorcise {_target(target)} will record {result.culprit[:7]} as introduced_in")


def info(message: str) -> None:
    console.print(Text(message))


def _requirement(finding: Finding) -> str:
    return f"{finding.req_type.value} {finding.name or 'unknown'}"


def exorcise_plan(exorcism: ExorcisePlan) -> None:
    verification = exorcism.verification
    header("exorcise", exorcism.repo.name, f"@ {exorcism.sha[:7]}", config.IMAGE)
    before = verification.before.first_failure
    if verification.nothing_to_fix:
        console.print("The committed README already passes in a fresh container. Nothing to fix.",
                      style="green")
        return
    if before:
        console.print(Text(f"❌ before: step {before.step.index} `{before.step.command}` fails"))
    if verification.verified and verification.after:
        console.print(Text("✅ after: every README step passes with the patch (same commit, fresh container)"))
    if verification.inconclusive:
        error(verification.inconclusive)
    table = Table(title="Requirements")
    for column in ("Requirement", "Evidence", "Cause", "Fix verified", "Introduced in"):
        table.add_column(column, overflow="fold")
    for finding in verification.verified:
        label = "✅ alone" if finding.status == Status.VERIFIED_FIX else "✅ with the other fixes"
        introduced = exorcism.introduced_in.get(requirement_id(finding.req_type, finding.name)) if finding.name else None
        table.add_row(Text(_requirement(finding)), Text(finding.evidence, style="red"),
                      finding.cause.value, Text(label, style="green"),
                      Text(introduced, style="cyan") if introduced else Text("not traced", style="dim"))
    for finding in verification.unverified:
        table.add_row(Text(_requirement(finding)), Text(finding.evidence, style="red"),
                      finding.cause.value, Text("❌ needs a human", style="yellow"), "—")
    if verification.verified or verification.unverified:
        console.print(table)
    if exorcism.diff:
        console.print(Text("\nProposed changes", style="bold"))
        console.print(Syntax(exorcism.diff, "diff", theme="ansi_dark", background_color="default"))
    if exorcism.workflow_note:
        console.print(Text(exorcism.workflow_note, style="yellow"))
    if verification.unverified and not verification.verified:
        console.print("No fix was verified, so no files will be written.", style="yellow")


def exorcise_written(paths: list[Path], repo: Path = Path(".")) -> None:
    for path in paths:
        console.print(Text(f"✅ wrote {path}"), soft_wrap=True)
    hint(f"Review with git -C {_target(repo)} diff, then commit yourself; Phantom never commits.")


def doctor_results(results: list[CheckResult]) -> None:
    header("doctor")
    for result in results:
        icon = {CHECK_PASS: "✅", FAIL: "❌"}.get(result.state, "⚠️ ")
        console.print(Text(f"{icon} {result.id}  {result.detail}"))
        if result.hint:
            console.print(Text(f"   hint: {result.hint}", style="dim"))
    failed = sum(result.state == FAIL for result in results)
    warned = sum(result.state == WARN for result in results)
    passed = len(results) - failed - warned
    summary = Text()
    for count, label, style in ((passed, "passed", "green"), (failed, "failed", "red"),
                                (warned, "warnings" if warned != 1 else "warning", "yellow")):
        if summary:
            summary.append(" · ", style="dim")
        summary.append(f"{count} {label}", style=style if count else "dim")
    console.print(summary)
    if failed:
        hint("Fix the failed checks above (see each hint), then run phantom doctor again.")
