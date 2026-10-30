"""All terminal presentation, including machine-readable output."""

from contextlib import nullcontext
from dataclasses import asdict
import json
from typing import ContextManager

from rich.console import Console
from rich.console import Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ghostdev import config
from ghostdev.bisect import BisectResult, FAIL, PASS
from ghostdev.models import Finding, RunResult
from ghostdev.verify import PatchError, render_fix

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


def explanation_label(ai_note: str | None) -> str:
    """Name the model honestly: the fallback model is not Gemma."""
    model = (ai_note or "").removeprefix("explained by ")
    return "Gemma explanation" if model.startswith("gemma") else f"AI explanation ({model or 'model'})"


def haunt_result(result: RunResult, finding: Finding | None, ai_note: str | None = None,
                 json_output: bool = False) -> None:
    if json_output:
        print(json.dumps({"run": asdict(result), "finding": finding.to_dict() if finding else None,
                          "ai_note": ai_note}))
        return
    console.print(Text(config.APP_NAME, style="bold"))
    for step in result.steps:
        icon = "✅" if step.exit_code == 0 and not step.timed_out else "❌"
        console.print(Text(f"{icon} step {step.step.index}  {step.step.command}"))
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


def bisect_result(result: BisectResult) -> None:
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
    details.add_row("AI explanation" if result.explanation else "Explanation",
                    Text(result.explanation or "AI explanation unavailable"))
    parts: list[object] = [details]
    if commit:
        parts.append(Text("\n" + commit.stat))
    if commit and commit.hunk:
        parts.append(Syntax(commit.hunk, "diff", theme="ansi_dark", background_color="default"))
    console.print(Panel(Group(*parts), title="First bad commit"))


def not_implemented() -> None:
    console.print("not implemented yet")
