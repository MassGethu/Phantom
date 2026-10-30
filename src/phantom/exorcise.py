"""Orchestrate haunt → verify → review → write; never commits in the target repository."""

from dataclasses import dataclass
import difflib
from pathlib import Path
from typing import Any

from phantom import config, copilot, lockfile
from phantom.bisect import load_last
from phantom.diagnose import diagnose
from phantom.models import Finding
from phantom.parse import committed_readme, extract_steps_from_text
from phantom.runner import RunFn, resolve_sha, run_steps
from phantom.verify import Verification, render_fix, verify_readme


class DirtyReadmeError(Exception):
    """The working-tree README differs from the committed one we verified against."""


@dataclass
class ExorcisePlan:
    repo: Path
    sha: str
    readme_name: str
    original: str
    verification: Verification
    introduced_in: dict[str, str | None]
    lock: dict[str, Any] | None
    diff: str
    workflow: str | None = None
    workflow_note: str | None = None

    @property
    def readme_path(self) -> Path:
        return self.repo / self.readme_name

    @property
    def lock_path(self) -> Path:
        return self.repo / lockfile.LOCK_NAME

    @property
    def workflow_path(self) -> Path:
        return self.repo / copilot.WORKFLOW_PATH


def _guard_readme(repo: Path, name: str, committed: str) -> None:
    path = repo / name
    try:
        current = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DirtyReadmeError(f"Cannot read {name} in the working tree: {error}") from error
    if current != committed:
        raise DirtyReadmeError(f"{name} has uncommitted changes. Commit or stash them first; "
                               "exorcise only patches committed documentation.")


def _introduced_in(repo: Path, finding: Finding) -> str | None:
    """Use a cached bisect culprit only when it traced this same requirement."""
    last = load_last(repo)
    if not last or not last.get("culprit"):
        return None
    if last.get("name") != finding.name or last.get("req_type") != finding.req_type.value:
        return None
    return str(last["culprit"])[:7]


def plan(path: Path, rev: str = "HEAD", run: RunFn = run_steps, use_ai: bool = True) -> ExorcisePlan:
    repo = path.resolve()
    sha = resolve_sha(repo, rev)
    name, original = committed_readme(repo, sha)
    _guard_readme(repo, name, original)
    steps = extract_steps_from_text(original)
    if not steps:
        raise ValueError("No runnable fenced shell setup steps found in the committed README.")
    explain = (lambda finding: diagnose(finding, steps, repo, sha)[0]) if use_ai else None
    verification = verify_readme(repo, sha, original, run, explain)
    introduced = {lockfile.requirement_id(f.req_type, f.name): _introduced_in(repo, f)
                  for f in verification.verified if f.name}
    lock = None
    if verification.verified and verification.patched_text:
        entries = [lockfile.entry(f, render_fix(f.fix), verification.command_hash,
                                  introduced[lockfile.requirement_id(f.req_type, f.name)])
                   for f in verification.verified if f.fix and f.name]
        existing = lockfile.load(repo / lockfile.LOCK_NAME)
        after = verification.after
        lock = lockfile.merge(existing, after.image, after.platform, entries)
    patched = verification.patched_text or original
    diff = "".join(difflib.unified_diff(original.splitlines(keepends=True),
                                        patched.splitlines(keepends=True),
                                        f"a/{name}", f"b/{name}"))
    return ExorcisePlan(repo, sha, name, original, verification, introduced, lock, diff)


def add_copilot(exorcism: ExorcisePlan) -> ExorcisePlan:
    """Attach a workflow built only from the patched steps that just passed."""
    after = exorcism.verification.after
    if not (exorcism.verification.verified and after and after.passed):
        exorcism.workflow_note = "No Copilot workflow: the patched README did not fully pass."
    elif exorcism.workflow_path.exists():
        exorcism.workflow_note = f"{copilot.WORKFLOW_PATH} already exists; it is left unchanged."
    else:
        exorcism.workflow = copilot.render_workflow([result.step.command for result in after.steps],
                                                    config.IMAGE)
        exorcism.diff += "".join(difflib.unified_diff(
            [], exorcism.workflow.splitlines(keepends=True), "/dev/null", f"b/{copilot.WORKFLOW_PATH}"))
    return exorcism


def apply(exorcism: ExorcisePlan) -> list[Path]:
    """Write README and lock to the working tree. The user reviews and commits them."""
    patched = exorcism.verification.patched_text
    if not patched or exorcism.lock is None:
        return []
    # Re-check right before writing in case the README changed during verification.
    _guard_readme(exorcism.repo, exorcism.readme_name, exorcism.original)
    exorcism.readme_path.write_text(patched, encoding="utf-8", newline="")
    exorcism.lock_path.write_text(lockfile.dump(exorcism.lock), encoding="utf-8")
    written = [exorcism.readme_path, exorcism.lock_path]
    if exorcism.workflow:
        exorcism.workflow_path.parent.mkdir(parents=True, exist_ok=True)
        # Mode "x" refuses to overwrite a workflow created since the plan was shown.
        with exorcism.workflow_path.open("x", encoding="utf-8") as handle:
            handle.write(exorcism.workflow)
        written.append(exorcism.workflow_path)
    return written
