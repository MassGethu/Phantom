"""Fixed fix templates, README patching and before/after verification on the same commit."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
import shlex

from ghostdev import config
from ghostdev.models import Finding, Fix, ReqType, RunResult, Status, Step
from ghostdev.parse import extract_steps_from_text, steps_hash
from ghostdev.rules import classify
from ghostdev.runner import RunFn

COMMENT = "# required: {name} (added by ghostdev)"
INSERTED_LINES = 2
ExplainFn = Callable[[Finding], Finding]


class PatchError(ValueError):
    """The README could not be patched so that re-parsing extracts the new command."""


def render_fix(fix: Fix) -> str:
    """The only place executable fix commands are created; every value is shell-quoted."""
    if fix.type == ReqType.ENV_VAR:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", fix.name):
            raise PatchError(f"Unsafe environment variable name: {fix.name!r}")
        return f"export {fix.name}={shlex.quote(fix.example_value or 'changeme')}"
    if fix.type == ReqType.FILE:
        path = Path(fix.name)
        if path.is_absolute() or ".." in path.parts or fix.name.startswith("-") or "\n" in fix.name:
            raise PatchError(f"Unsafe file name: {fix.name!r}")
        return f"cp {shlex.quote(fix.name + '.example')} {shlex.quote(fix.name)}"
    raise PatchError(f"No fix template for {fix.type.value} requirements.")


def patch_readme(readme_text: str, failing_step: Step, fix: Fix) -> str:
    """Insert the rendered fix on its own line directly before the failing step's source line."""
    command = render_fix(fix)
    lines = readme_text.splitlines(keepends=True)
    index = failing_step.source_line - 1
    if not 0 <= index < len(lines):
        raise PatchError(f"README line {failing_step.source_line} does not exist.")
    target = lines[index]
    match = re.match(r"(\s*)((?:[$%] )?)(.*)", target.rstrip("\r\n"))
    indent, prompt, text = match[1], match[2], match[3]
    if not failing_step.command.startswith(text.rstrip("\\").rstrip()):
        raise PatchError(f"README line {failing_step.source_line} is not step {failing_step.index}.")
    newline = target[len(target.rstrip("\r\n")):] or "\n"
    # The comment has no prompt: bash blocks skip comments and console blocks skip
    # unprompted output lines. The command keeps the step's prompt so console blocks parse it.
    inserted = [f"{indent}{COMMENT.format(name=fix.name)}{newline}", f"{indent}{prompt}{command}{newline}"]
    patched = "".join(lines[:index] + inserted + lines[index:])
    _check_extracted(patched, failing_step, command)
    return patched


def _check_extracted(patched: str, failing_step: Step, command: str) -> None:
    steps = extract_steps_from_text(patched)
    shifted = failing_step.source_line + INSERTED_LINES
    position = next((i for i, step in enumerate(steps) if step.source_line == shifted), None)
    if position is None or position == 0 or steps[position - 1].command != command:
        raise PatchError("The patched README would not run the fix before the failing step.")


@dataclass
class Candidate:
    """A fix aimed at a step of the original README (original line numbers)."""
    step: Step
    fix: Fix
    finding: Finding


def apply_fixes(readme_text: str, candidates: list[Candidate]) -> str:
    # Patching bottom-up keeps every original line number valid while inserting; a second
    # fix for the same step targets that step's line, now below the first fix's lines.
    applied: dict[int, int] = {}
    for candidate in sorted(candidates, key=lambda c: c.step.source_line, reverse=True):
        line = candidate.step.source_line
        target = replace(candidate.step, source_line=line + INSERTED_LINES * applied.get(line, 0))
        readme_text = patch_readme(readme_text, target, candidate.fix)
        applied[line] = applied.get(line, 0) + 1
    return readme_text


def original_step(patched_step: Step, original: list[Step], candidates: list[Candidate]) -> Step | None:
    """Map a step of the patched README back to the original; None means a fix line failed."""
    starts = [candidate.step.source_line for candidate in candidates]
    for step in original:
        shift = INSERTED_LINES * sum(1 for start in starts if start <= step.source_line)
        if step.source_line + shift == patched_step.source_line:
            return step
    return None


def verify_fix(repo: Path, sha: str, readme_text: str, patched_text: str, run: RunFn,
               before: RunResult | None = None) -> tuple[Status | None, RunResult]:
    """VERIFIED_FIX only when the original fails and the re-parsed patch passes on the same sha."""
    before = before or run(repo, sha, extract_steps_from_text(readme_text))
    after = run(repo, sha, extract_steps_from_text(patched_text))
    if before.infra_error or before.passed or after.infra_error or not after.passed:
        return None, after
    return Status.VERIFIED_FIX, after


@dataclass
class Verification:
    before: RunResult
    after: RunResult | None = None
    patched_text: str | None = None
    command_hash: str | None = None
    verified: list[Finding] = field(default_factory=list)
    # Proposed or diagnosed requirements that no rerun proved; they need a human.
    unverified: list[Finding] = field(default_factory=list)
    inconclusive: str | None = None

    @property
    def nothing_to_fix(self) -> bool:
        return self.before.passed


def _finding_for(step: Step, finding: Finding) -> Finding:
    """Report the requirement against the original README step, not the patched numbering."""
    return replace(finding, step=replace(finding.step, step=step))


def verify_readme(repo: Path, sha: str, readme_text: str, run: RunFn,
                  explain: ExplainFn | None = None, max_rounds: int = config.MAX_ROUNDS) -> Verification:
    """Diagnose, fix and rerun until setup passes, a requirement needs a human, or rounds run out."""
    original = extract_steps_from_text(readme_text)
    before = run(repo, sha, original)
    outcome = Verification(before)
    if before.infra_error:
        outcome.inconclusive = before.infra_error
        return outcome
    if before.passed:
        return outcome
    candidates: list[Candidate] = []
    current = before
    for _ in range(max_rounds):
        failure = current.first_failure
        if failure is None:
            outcome.inconclusive = "Setup ended before all commands completed."
            break
        finding = classify(failure, repo, sha)
        if explain:
            finding = explain(finding)
        if finding.status == Status.INCONCLUSIVE:
            outcome.inconclusive = f"Inconclusive run: {finding.evidence or 'step timed out'}"
            break
        step = original_step(failure.step, original, candidates)
        if step is None or finding.fix is None:
            # Either an inserted fix line failed or no template exists: a human must decide.
            outcome.unverified.append(finding)
            break
        if any(c.fix == finding.fix and c.step == step for c in candidates):
            outcome.unverified.append(_finding_for(step, finding))
            break
        try:
            patched = apply_fixes(readme_text, [*candidates, Candidate(step, finding.fix, finding)])
        except PatchError:
            outcome.unverified.append(_finding_for(step, finding))
            break
        candidates.append(Candidate(step, finding.fix, finding))
        status, current = verify_fix(repo, sha, readme_text, patched, run, before)
        outcome.after = current
        if current.infra_error:
            outcome.inconclusive = current.infra_error
            break
        if status:
            return _accept(outcome, repo, sha, readme_text, run, candidates, patched)
    # A partial improvement (an earlier step now passes) is not a verified fix.
    outcome.unverified = [_finding_for(c.step, c.finding) for c in candidates] + outcome.unverified
    return outcome


def _accept(outcome: Verification, repo: Path, sha: str, readme_text: str, run: RunFn,
            candidates: list[Candidate], patched: str) -> Verification:
    if len(candidates) > 1:
        # R6: one change at a time. The first candidate alone already failed in round one;
        # later ones are tested alone too, and one that suffices replaces the combination.
        for candidate in candidates[1:]:
            alone = apply_fixes(readme_text, [candidate])
            status, after = verify_fix(repo, sha, readme_text, alone, run, outcome.before)
            if status:
                return _record(outcome, [candidate], alone, after, Status.VERIFIED_FIX)
        return _record(outcome, candidates, patched, outcome.after, Status.VERIFIED_TOGETHER)
    return _record(outcome, candidates, patched, outcome.after, Status.VERIFIED_FIX)


def _record(outcome: Verification, candidates: list[Candidate], patched: str,
            after: RunResult | None, status: Status) -> Verification:
    # R5: only this module assigns VERIFIED_* statuses.
    outcome.after = after
    outcome.patched_text = patched
    outcome.command_hash = steps_hash(extract_steps_from_text(patched))
    outcome.verified = [replace(_finding_for(c.step, c.finding), status=status) for c in candidates]
    outcome.unverified = []
    return outcome
