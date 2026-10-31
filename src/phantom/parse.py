"""Deterministic extraction of fenced README shell commands."""

import hashlib
import json
from pathlib import Path
import re
import subprocess

from phantom import config
from phantom.models import Step

LANGUAGES = {"", "bash", "sh", "shell", "console", "zsh"}
# `git clone URL [&& cd DIR] [&&]`; whatever follows the last && still runs.
CLONE = re.compile(r"git\s+clone(?:\s[^&]*)??(?P<cd>&&\s*cd\s+[^\s&;|]+\s*)?(?:&&\s*|$)")


def _diagram_start(line: str) -> bool:
    """Recognize drawing edges, not Unicode that occurs inside a shell command."""
    if line and "\u2500" <= line[0] <= "\u257f":
        return True
    return re.fullmatch(r"\+(?:[-=]+\+)+(?:\s+\+(?:[-=]+\+)+)*", line) is not None


def find_readme(repo: Path) -> Path:
    matches = sorted(path for path in repo.iterdir()
                     if path.is_file() and path.name.lower() == "readme.md")
    if not matches:
        raise FileNotFoundError("No README.md found at the repository root.")
    return matches[0]


def committed_readme(repo: Path, sha: str) -> tuple[str, str]:
    """Return (filename, text) of the README committed at sha, matching the copied source."""
    listing = subprocess.run(["git", "-C", str(repo), "ls-tree", "--name-only", sha],
                             capture_output=True, text=True, check=True, timeout=config.GIT_TIMEOUT)
    names = sorted(name for name in listing.stdout.splitlines() if name.lower() == "readme.md")
    if not names:
        raise FileNotFoundError("No README.md is committed at the repository root.")
    # Bytes, not text mode, so CRLF line endings survive a later README patch.
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{names[0]}"],
                           capture_output=True, check=True, timeout=config.GIT_TIMEOUT)
    return names[0], shown.stdout.decode("utf-8")


def extract_steps(repo: Path) -> list[Step]:
    return extract_steps_from_text(find_readme(repo).read_text(encoding="utf-8"))


def extract_steps_from_text(readme_text: str) -> list[Step]:
    steps: list[Step] = []
    fence: str | None = None
    language = ""
    pending: list[str] = []
    source_line = 0
    after_clone = False
    enter_repo = False
    transcript: bool | None = None
    diagram = False

    for number, raw in enumerate(readme_text.splitlines(), 1):
        opening = re.match(r"^\s{0,3}(`{3,}|~{3,})([^\n]*)$", raw)
        if fence is None:
            if opening:
                fence = opening[1]
                language = opening[2].strip().lower()
                after_clone = False
                transcript = None
                diagram = False
            continue
        if re.fullmatch(r"\s{0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", raw):
            fence = None
            pending = []
            continue
        if language not in LANGUAGES or diagram:
            continue
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        prompted = line.startswith(("$ ", "% "))
        if transcript is None:
            # Untagged blocks can be architecture diagrams. Skip the entire block,
            # including labels below its border that otherwise look like commands.
            if language == "" and _diagram_start(line):
                diagram = True
                continue
            # A block that opens with a prompt is a transcript: unprompted lines are output.
            transcript = language == "console" or prompted
        if transcript and not prompted and not pending:
            continue
        if prompted:
            line = line[2:]
        if not pending:
            source_line = number
        continued = line.endswith("\\")
        pending.append(line[:-1].rstrip() if continued else line)
        if continued:
            continue
        command = " ".join(pending)
        pending = []
        clone = CLONE.match(command)
        if clone:
            # The repository is already at /repo, so the clone becomes "be at the repo root".
            enter_repo = bool(clone["cd"])
            after_clone = not clone["cd"]
            command = command[clone.end():].strip()
            if not command:
                continue
        elif after_clone and re.match(r"^cd\s+\S+\s*$", command):
            after_clone = False
            enter_repo = True
            continue
        after_clone = False
        steps.append(Step(len(steps) + 1, command, source_line, enter_repo))
        enter_repo = False
    return steps


def steps_hash(steps: list[Step]) -> str:
    """Identify the exact commands a run executed, for caches and the lock file."""
    # Steps that start from the repo root hash differently; other hashes stay as before.
    commands = [["in_repo", step.command] if step.in_repo else step.command for step in steps]
    return hashlib.sha256(json.dumps(commands).encode()).hexdigest()
