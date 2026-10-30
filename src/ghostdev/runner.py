"""Execute committed source in disposable Docker containers, never on the host."""

from collections.abc import Callable
from pathlib import Path
import re
import subprocess
import uuid

from ghostdev import config
from ghostdev.models import RunResult, Step, StepResult

RunFn = Callable[[Path, str, list[Step]], RunResult]


def _command(args: list[str], *, data: bytes | None = None,
             timeout: int = config.DOCKER_TIMEOUT) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, input=data, capture_output=True, timeout=timeout, check=True)


def _decode(data: bytes | str | None) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def git_output(repo: Path, *args: str) -> str:
    """Run a read-only Git query against the target repository."""
    return _decode(_command(["git", "-C", str(repo), *args], timeout=config.GIT_TIMEOUT).stdout)


def resolve_sha(repo: Path, rev: str = "HEAD") -> str:
    return git_output(repo, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}").strip()


def image_digest() -> str:
    args = ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", config.IMAGE]
    try:
        digest = _decode(_command(args).stdout).strip()
    except subprocess.CalledProcessError:
        _command(["docker", "pull", config.IMAGE])
        digest = _decode(_command(args).stdout).strip()
    if "@sha256:" not in digest:
        raise ValueError("Docker image has no repository digest; pull a tagged image and retry.")
    return digest


def platform_str(image: str = config.IMAGE) -> str:
    return _decode(_command(["docker", "image", "inspect", "--format",
                             "{{.Os}}/{{.Architecture}}", image]).stdout).strip()


def build_script(steps: list[Step]) -> str:
    lines = ["exec 2>&1", "export PYTHONUNBUFFERED=1", "cd /repo || exit 125"]
    # One bash process preserves exports, working directory and shell state between steps.
    for step in steps:
        lines.extend([f"echo '::ghost-step {step.index}::'", step.command,
                      f"__rc=$?; printf '\\n::ghost-rc {step.index} %s::\\n' \"$__rc\"; [ $__rc -ne 0 ] && exit $__rc"])
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def parse_output(output: str, steps: list[Step], *, timed_out: bool = False,
                 process_code: int | None = None) -> list[StepResult]:
    by_index = {step.index: step for step in steps}
    results: list[StepResult] = []
    current: StepResult | None = None
    for line in output.splitlines(keepends=True):
        start = re.fullmatch(r"::ghost-step (\d+)::\s*", line)
        end = re.fullmatch(r"::ghost-rc (\d+) (-?\d+)::\s*", line)
        if start and int(start[1]) in by_index:
            current = StepResult(by_index[int(start[1])], None, "")
            results.append(current)
        elif end and current and int(end[1]) == current.step.index:
            current.exit_code = int(end[2])
            current = None
        elif current:
            current.output += line
    if current:
        current.timed_out = timed_out
        if not timed_out and process_code:
            current.exit_code = process_code
    return results


def error_message(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "Docker or Git is not installed. Install both and retry."
    if isinstance(error, subprocess.TimeoutExpired):
        return "Container preparation timed out. Check Docker and network access, then retry."
    if isinstance(error, subprocess.CalledProcessError):
        detail = _decode(error.stderr or error.stdout).strip()
        if any(text in detail.lower() for text in
               ("docker.sock", "cannot connect to the docker daemon", "is the docker daemon running",
                "failed to connect to the docker api", "the daemon is running")):
            return "Docker isn't running. Start Docker Desktop and retry."
        return f"Container preparation failed: {detail.splitlines()[-1] if detail else 'command failed'}"
    return str(error)


def run_steps(repo: Path, sha: str, steps: list[Step],
              timeout: int = config.RUN_TIMEOUT) -> RunResult:
    container = f"ghost-{uuid.uuid4().hex[:8]}"
    result = RunResult(sha, config.IMAGE, "unknown", [], False)
    try:
        result.image = image_digest()
        result.platform = platform_str(result.image)
        _command(["docker", "run", "-d", "--rm=false", "--name", container,
                  "--network", "bridge", result.image, "sleep", "infinity"])
        _command(["docker", "exec", container, "mkdir", "-p", "/repo"])
        # Copy an archive rather than mounting a host directory into the container.
        archive = _command(["git", "-C", str(repo), "archive", sha], timeout=config.GIT_TIMEOUT)
        _command(["docker", "cp", "-", f"{container}:/repo"], data=archive.stdout)
        try:
            execution = subprocess.run(["docker", "exec", "-i", container, "bash", "-s"],
                                       input=build_script(steps).encode(), capture_output=True,
                                       timeout=timeout, check=False)
            output = _decode(execution.stdout) + _decode(execution.stderr)
            result.steps = parse_output(output, steps, process_code=execution.returncode)
            result.passed = (execution.returncode == 0 and len(result.steps) == len(steps)
                             and all(s.exit_code == 0 for s in result.steps))
            if not result.steps and not result.passed:
                result.infra_error = f"Container execution failed: {output.strip()}"
        except subprocess.TimeoutExpired as error:
            result.steps = parse_output(_decode(error.stdout) + _decode(error.stderr),
                                        steps, timed_out=True)
            if not result.steps:
                result.infra_error = "Container execution timed out before any setup step started."
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        result.infra_error = error_message(error)
    finally:
        try:
            cleanup = subprocess.run(["docker", "rm", "-f", container], capture_output=True,
                                     timeout=config.GIT_TIMEOUT, check=False)
            if cleanup.returncode and "No such container" not in _decode(cleanup.stderr):
                result.infra_error = result.infra_error or "Container cleanup failed. Check Docker Desktop."
                result.passed = False
        except (OSError, subprocess.SubprocessError) as error:
            result.infra_error = result.infra_error or error_message(error)
            result.passed = False
    return result
