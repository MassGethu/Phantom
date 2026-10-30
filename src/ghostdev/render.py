"""All terminal presentation, including machine-readable output."""

from contextlib import nullcontext
from dataclasses import asdict
import json
import shlex
from typing import ContextManager

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ghostdev import config
from ghostdev.models import Finding, ReqType, RunResult

console = Console()


def running(json_output: bool) -> ContextManager[object]:
    return nullcontext() if json_output else console.status("Running README setup in a fresh container…")


def error(message: str, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps({"status": "inconclusive", "error": message}))
    else:
        console.print(Text(message, style="red"))


def suggested_fix(finding: Finding) -> str | None:
    fix = finding.fix
    if fix is None:
        return None
    if fix.type == ReqType.ENV_VAR:
        return f"export {fix.name}={shlex.quote(fix.example_value or 'changeme')}"
    if fix.type == ReqType.FILE:
        return f"cp {shlex.quote(fix.name + '.example')} {shlex.quote(fix.name)}"
    return None


def haunt_result(result: RunResult, finding: Finding | None, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps({"run": asdict(result), "finding": finding.to_dict() if finding else None}))
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
        details.add_row("Gemma explanation", Text(finding.explanation))
    else:
        details.add_row("Explanation", "AI explanation unavailable")
    command = suggested_fix(finding)
    if command:
        details.add_row("Suggested fix (not yet verified)", Text(command))
    console.print(Panel(details, title="Setup finding"))


def not_implemented() -> None:
    console.print("not implemented yet")
