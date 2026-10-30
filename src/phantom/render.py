"""All terminal presentation, including machine-readable output."""

from contextlib import nullcontext
from dataclasses import asdict
import json
from pathlib import Path
from typing import ContextManager

from rich.console import Console
from rich.console import Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from phantom import config
from phantom.bisect import BisectResult, FAIL, PASS
from phantom.doctor import CheckResult, FAIL, PASS as CHECK_PASS
from phantom.exorcise import ExorcisePlan
from phantom.lockfile import requirement_id
from phantom.models import Finding, RunResult, Status
from phantom.verify import PatchError, render_fix

console = Console()


def running(json_output: bool,
            message: str = "Running README setup in a fresh container…") -> ContextManager[object]:
    return nullcontext() if json_output else console.status(message)


def error(message: str, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps({"status": "inconclusive", "error": message}))
    else:
        console.print(Text(message, style="red"))


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
                 json_output: bool = False, flags: list[str] | None = None) -> None:
    flags = flags or []
    if json_output:
        print(json.dumps({"run": asdict(result), "finding": finding.to_dict() if finding else None,
                          "ai_note": ai_note, "risk_flags": flags}))
        return
    console.print(Text(config.APP_NAME, style="bold"))
    for step in result.steps:
        icon = "✅" if step.exit_code == 0 and not step.timed_out else "❌"
        console.print(Text(f"{icon} step {step.step.index}  {step.step.command}"))
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
    details.add_row("Status", Text(finding.status.value))
    details.add_row("Type / Name", Text(f"{finding.req_type.value} / {finding.name or 'unknown'}"))
    details.add_row("Evidence", Text(finding.evidence or "No output captured."))
    details.add_row("Cause", Text(finding.cause.value))
    if finding.explanation:
        details.add_row(explanation_label(ai_note), Text(finding.explanation))
    else:
        details.add_row("Explanation", Text(ai_note or "AI explanation unavailable"))
    command = suggested_fix(finding)
    if command:
        details.add_row("Suggested fix (not yet verified)", Text(command))
    console.print(Panel(details, title="Setup finding"))


def bisect_probe(sha: str, verdict: str, cached: bool) -> None:
    icon = {PASS: "✅", FAIL: "❌"}.get(verdict, "⚠️ ")
    suffix = " (cached)" if cached else ""
    label = "skipped: infrastructure" if verdict not in (PASS, FAIL) else verdict
    console.print(Text(f"{icon} {sha[:7]}  {label}{suffix}"))


def bisect_result(result: BisectResult, use_ai: bool = True) -> None:
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
    parts: list[object] = [details]
    if commit:
        parts.append(Text("\n" + commit.stat))
    if commit and commit.hunk:
        parts.append(Syntax(commit.hunk, "diff", theme="ansi_dark", background_color="default"))
    console.print(Panel(Group(*parts), title="First bad commit"))


def info(message: str) -> None:
    console.print(Text(message))


def _requirement(finding: Finding) -> str:
    return f"{finding.req_type.value} {finding.name or 'unknown'}"


def exorcise_plan(exorcism: ExorcisePlan) -> None:
    verification = exorcism.verification
    console.print(Text(f"{config.APP_NAME} exorcise  {exorcism.sha[:7]}", style="bold"))
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
        table.add_row(Text(_requirement(finding)), Text(finding.evidence), finding.cause.value,
                      label, introduced or "not traced")
    for finding in verification.unverified:
        table.add_row(Text(_requirement(finding)), Text(finding.evidence), finding.cause.value,
                      "❌ needs a human", "—")
    if verification.verified or verification.unverified:
        console.print(table)
    if exorcism.diff:
        console.print(Syntax(exorcism.diff, "diff", theme="ansi_dark", background_color="default"))
    if exorcism.workflow_note:
        console.print(Text(exorcism.workflow_note, style="yellow"))
    if verification.unverified and not verification.verified:
        console.print("No fix was verified, so no files will be written.", style="yellow")


def exorcise_written(paths: list[Path]) -> None:
    for path in paths:
        console.print(Text(f"wrote {path}"))
    console.print("Review the changes and commit them yourself; Phantom never commits.")


def doctor_results(results: list[CheckResult]) -> None:
    console.print(Text(f"{config.APP_NAME} doctor", style="bold"))
    for result in results:
        icon = {CHECK_PASS: "✅", FAIL: "❌"}.get(result.state, "⚠️ ")
        console.print(Text(f"{icon} {result.id}  {result.detail}"))
        if result.hint:
            console.print(Text(f"   hint: {result.hint}", style="dim"))
    failed = sum(result.state == FAIL for result in results)
    console.print(f"{failed} of {len(results)} checks failed." if failed else "All checks passed.",
                  style="red" if failed else "green")
