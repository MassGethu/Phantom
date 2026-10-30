"""Deterministic extraction of fenced README shell commands."""

from pathlib import Path
import re
import subprocess

from ghostdev import config
from ghostdev.models import Step

LANGUAGES = {"", "bash", "sh", "shell", "console", "zsh"}


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
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{names[0]}"],
                           capture_output=True, text=True, check=True, timeout=config.GIT_TIMEOUT)
    return names[0], shown.stdout


def extract_steps(repo: Path) -> list[Step]:
    return extract_steps_from_text(find_readme(repo).read_text(encoding="utf-8"))


def extract_steps_from_text(readme_text: str) -> list[Step]:
    steps: list[Step] = []
    fence: str | None = None
    language = ""
    pending: list[str] = []
    source_line = 0
    after_clone = False

    for number, raw in enumerate(readme_text.splitlines(), 1):
        opening = re.match(r"^\s{0,3}(`{3,}|~{3,})([^\n]*)$", raw)
        if fence is None:
            if opening:
                fence = opening[1]
                language = opening[2].strip().lower()
                after_clone = False
            continue
        if re.fullmatch(r"\s{0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", raw):
            fence = None
            pending = []
            continue
        if language not in LANGUAGES:
            continue
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        prompted = line.startswith(("$ ", "% "))
        if language == "console" and not prompted and not pending:
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
        if re.match(r"^git\s+clone(?:\s|$)", command):
            after_clone = True
            continue
        if after_clone and re.match(r"^cd\s+\S+\s*$", command):
            after_clone = False
            continue
        after_clone = False
        steps.append(Step(len(steps) + 1, command, source_line))
    return steps
