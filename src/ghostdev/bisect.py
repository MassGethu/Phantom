"""First-bad-commit search over first-parent history, with boundary checks and a result cache."""

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from ghostdev import config
from ghostdev.diagnose import GenerateFn, explain_commit, generate
from ghostdev.models import Finding, RunResult, Status, Step
from ghostdev.parse import committed_readme, extract_steps_from_text, steps_hash
from ghostdev.rules import classify, mentions
from ghostdev.runner import (RunFn, error_message, git_output, image_digest, platform_str,
                             resolve_sha, run_steps)

PASS, FAIL, SKIP = "pass", "fail", "skip"
CACHE_VERSION = 1
EnvironmentFn = Callable[[], tuple[str, str]]
ProbeFn = Callable[[str, str, bool], None]


class BoundaryError(Exception):
    """--good does not pass or --bad does not fail, so the search would be meaningless."""


class InconclusiveError(Exception):
    """A boundary could not be tested because of infrastructure, not repository code."""


@dataclass
class Probe:
    sha: str
    verdict: str
    finding: Finding | None = None
    cached: bool = False


@dataclass
class CommitInfo:
    sha: str
    author: str
    date: str
    subject: str
    stat: str
    hunk: str | None


@dataclass
class BisectResult:
    good: str
    bad: str
    probes: list[Probe]
    culprit: str | None
    # When skipped commits hide the culprit, it is one of these (oldest first).
    suspects: list[str] = field(default_factory=list)
    finding: Finding | None = None
    commit: CommitInfo | None = None
    explanation: str | None = None
    explanation_model: str | None = None
    cache_note: str | None = None


def environment() -> tuple[str, str]:
    try:
        image = image_digest()
        return image, platform_str(image)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise InconclusiveError(error_message(error)) from error


def verdict(result: RunResult, repo: Path) -> Probe:
    """pass = every step exits 0; skip = infrastructure; fail = a real, classified failure."""
    if result.infra_error:
        return Probe(result.sha, SKIP)
    if result.passed:
        return Probe(result.sha, PASS)
    failure = result.first_failure
    if failure is None:
        return Probe(result.sha, SKIP)
    finding = classify(failure, repo, result.sha)
    if finding.status == Status.INCONCLUSIVE:
        return Probe(result.sha, SKIP, finding)
    return Probe(result.sha, FAIL, finding)


class Cache:
    """results.json keyed by sha|image digest|platform|steps hash; skips are never cached."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.note: str | None = None
        self.entries: dict[str, Any] = {}
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != CACHE_VERSION or not isinstance(data.get("results"), dict):
                raise ValueError("unexpected cache format")
            self.entries = data["results"]
        except (OSError, ValueError, AttributeError) as error:
            self.note = f"Ignored unreadable cache {path} ({error}); it will be rewritten."

    def get(self, key: str) -> Probe | None:
        entry = self.entries.get(key)
        try:
            if entry["verdict"] not in (PASS, FAIL):
                return None
            finding = Finding.from_dict(entry["finding"]) if entry.get("finding") else None
            return Probe(entry["sha"], entry["verdict"], finding, cached=True)
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, key: str, probe: Probe) -> None:
        if probe.verdict == SKIP:
            return
        self.entries[key] = {"sha": probe.sha, "verdict": probe.verdict,
                             "finding": probe.finding.to_dict() if probe.finding else None}
        _write_json(self.path, {"version": CACHE_VERSION, "results": self.entries})


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=1)
    os.replace(temporary, path)


def first_parent_history(repo: Path, good: str, bad: str) -> list[str]:
    """Commits after good up to and including bad, oldest first."""
    if good not in git_output(repo, "rev-list", "--first-parent", bad).split():
        raise BoundaryError("--good must be an ancestor of --bad on its first-parent history.")
    return git_output(repo, "rev-list", "--first-parent", "--reverse", f"{good}..{bad}").split()


def oldest_commit(repo: Path, bad: str) -> str:
    return git_output(repo, "rev-list", "--first-parent", "--reverse", bad).split()[0]


def _midpoint(candidates: list[int], low: int, high: int) -> int:
    middle = (low + high) / 2
    return min(candidates, key=lambda index: (abs(index - middle), index))


def search(history: list[str], probe: Callable[[str], Probe],
           failing: Probe) -> tuple[str | None, list[str], Probe]:
    """Binary search where index -1 is the known-good commit and the last index is known bad.

    Returns (culprit, suspects, failing probe of the culprit or of the bad end of the range).
    """
    low, high = -1, len(history) - 1
    skipped: set[int] = set()
    while True:
        untested = [index for index in range(low + 1, high) if index not in skipped]
        if not untested:
            break
        index = _midpoint(untested, low, high)
        result = probe(history[index])
        if result.verdict == PASS:
            low = index
        elif result.verdict == FAIL:
            high, failing = index, result
        else:
            skipped.add(index)
    if any(low < index < high for index in skipped):
        return None, history[low + 1:high + 1], failing
    return history[high], [], failing


def commit_info(repo: Path, sha: str, name: str | None) -> tuple[CommitInfo, str]:
    header = git_output(repo, "show", "-s", "--format=%H%x00%an <%ae>%x00%aI%x00%s", sha).strip()
    full, author, date, subject = header.split("\x00")
    stat = git_output(repo, "show", "--stat", "--format=", sha).strip()
    diff = git_output(repo, "show", "--format=", "--unified=3", sha)
    hunk = matching_hunk(diff, name) if name else None
    return CommitInfo(full, author, date, subject, stat, hunk), diff


def matching_hunk(diff: str, name: str) -> str | None:
    """Return the hunk (with its file header) whose changed lines mention name.

    Code hunks win over example/sample files, which merely document the requirement.
    """
    hunks: list[list[str]] = []
    header = ""
    current: list[str] | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            header, current = line, None
        elif line.startswith("@@"):
            current = [header, line]
            hunks.append(current)
        elif current is not None:
            current.append(line)
    matches = [hunk for hunk in hunks
               if any(line[:1] in "+-" and mentions(name, line) for line in hunk[2:])]
    matches.sort(key=lambda hunk: bool(re.search(r"example|sample", hunk[0], re.I)))
    return "\n".join(matches[0]) if matches else None


def last_bisect_path(repo: Path) -> Path:
    digest = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()
    return config.CACHE_DIR / "last_bisect" / f"{digest}.json"


def save_last(repo: Path, result: BisectResult, steps: list[Step]) -> None:
    finding = result.finding
    _write_json(last_bisect_path(repo), {
        "repo": str(repo.resolve()), "good": result.good, "bad": result.bad,
        "culprit": result.culprit, "suspects": result.suspects, "steps_hash": steps_hash(steps),
        "req_type": finding.req_type.value if finding else None,
        "name": finding.name if finding else None,
    })


def load_last(repo: Path) -> dict[str, Any] | None:
    """Return the cached bisect summary for exorcise, or None when absent or unreadable."""
    path = last_bisect_path(repo)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_bisect(repo: Path, good: str | None = None, bad: str = "HEAD", *, use_ai: bool = True,
               run: RunFn = run_steps, env: EnvironmentFn = environment,
               generate_fn: GenerateFn = generate, on_probe: ProbeFn | None = None) -> BisectResult:
    repo = repo.resolve()
    bad_sha = resolve_sha(repo, bad)
    good_sha = resolve_sha(repo, good) if good else oldest_commit(repo, bad_sha)
    if good_sha == bad_sha:
        raise BoundaryError("--good and --bad are the same commit; pick an older --good.")
    history = first_parent_history(repo, good_sha, bad_sha)
    # One fixed check: the commands a newcomer follows today, parsed once from HEAD's README.
    steps = extract_steps_from_text(committed_readme(repo, resolve_sha(repo, "HEAD"))[1])
    if not steps:
        raise ValueError("No runnable fenced shell setup steps found in HEAD's committed README.")
    image, platform = env()
    cache = Cache(config.CACHE_DIR / "results.json")
    command_hash = steps_hash(steps)
    probes: list[Probe] = []

    def probe(sha: str) -> Probe:
        key = f"{sha}|{image}|{platform}|{command_hash}"
        result = cache.get(key)
        if result is None:
            result = verdict(run(repo, sha, steps), repo)
            cache.put(key, result)
        probes.append(result)
        if on_probe:
            on_probe(sha, result.verdict, result.cached)
        return result

    start = probe(good_sha)
    if start.verdict == SKIP:
        raise InconclusiveError("Could not test --good because of an infrastructure problem; retry.")
    if start.verdict == FAIL:
        raise BoundaryError("The known-good commit also fails, so pick an older --good.")
    end = probe(bad_sha)
    if end.verdict == SKIP:
        raise InconclusiveError("Could not test --bad because of an infrastructure problem; retry.")
    if end.verdict == PASS:
        raise BoundaryError("The --bad commit passes setup, so there is no break to trace.")
    culprit, suspects, failing = search(history, probe, end)
    result = BisectResult(good_sha, bad_sha, probes, culprit, suspects, failing.finding,
                          cache_note=cache.note)
    if culprit:
        result.commit, diff = commit_info(repo, culprit, failing.finding.name if failing.finding else None)
        # The model is asked once, after the search, never inside the loop.
        if use_ai and failing.finding:
            explained = explain_commit(diff, failing.finding, generate_fn)
            if explained:
                result.explanation_model, result.explanation = explained
    save_last(repo, result, steps)
    return result
