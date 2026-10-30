"""Check this machine against assumptions.lock; no Docker and no AI."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
from typing import Any

from phantom import config, lockfile

PASS, FAIL, WARN = "pass", "fail", "warn"
TCP_TIMEOUT = 1


@dataclass
class CheckResult:
    id: str
    state: str
    detail: str
    hint: str | None = None


def _version_output(tool: str) -> str:
    # The tool name comes from the lock; it runs without a shell, with a fixed argument.
    return subprocess.run([tool, "--version"], capture_output=True, text=True,
                          timeout=config.GIT_TIMEOUT).stdout


@dataclass
class Host:
    """What a check may observe; tests substitute every field."""
    environ: Mapping[str, str]
    cwd: Path
    which: Callable[[str], str | None] = shutil.which
    version_output: Callable[[str], str] = _version_output

    @classmethod
    def current(cls) -> "Host":
        return cls(os.environ, Path.cwd())


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(\d+(?:\.\d+)*)", text)
    return tuple(int(part) for part in match[1].split(".")) if match else None


def version_satisfies(found: str, spec: str) -> bool | None:
    """Support '>=X', '==X', '>X' and bare 'X' (prefix match); None means unparseable."""
    match = re.fullmatch(r"\s*(>=|==|>)?\s*v?(\d+(?:\.\d+)*)\s*", spec)
    have = _version_tuple(found)
    if not match or have is None:
        return None
    operator, want = match[1], tuple(int(part) for part in match[2].split("."))
    if operator == ">=":
        return have >= want
    if operator == ">":
        return have > want
    return have[:len(want)] == want if operator is None else have == want


def _tcp_open(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), TCP_TIMEOUT):
            return True
    except OSError:
        return False


def _outcome(item: Mapping[str, Any], ok: bool, passed: str, failed: str) -> CheckResult:
    if ok:
        return CheckResult(item["id"], PASS, passed)
    return CheckResult(item["id"], FAIL, failed, item.get("fix"))


def check(item: Mapping[str, Any], host: Host) -> CheckResult:
    name, spec = item["name"], item["check"]
    kind = spec["kind"]
    if kind == "env_set":
        return _outcome(item, name in host.environ, f"{name} is set (its value is not validated)",
                        f"{name} is not set")
    if kind == "file_exists":
        return _outcome(item, (host.cwd / name).exists(), f"{name} exists", f"{name} is missing")
    if kind == "which":
        tool = spec.get("name", name)
        return _outcome(item, host.which(tool) is not None, f"{tool} is on PATH",
                        f"{tool} is not on PATH")
    if kind == "tcp":
        port = str(spec.get("port", name))
        if not port.isdigit():
            return CheckResult(item["id"], WARN, f"invalid port {port!r}")
        return _outcome(item, _tcp_open(int(port)), f"something listens on localhost:{port}",
                        f"nothing listens on localhost:{port}")
    if kind == "version":
        tool, wanted = spec.get("tool", name), str(spec.get("spec", ""))
        if host.which(tool) is None:
            return _outcome(item, False, "", f"{tool} is not on PATH")
        try:
            found = host.version_output(tool)
        except (OSError, subprocess.SubprocessError) as error:
            return CheckResult(item["id"], WARN, f"could not run `{tool} --version`: {error}")
        ok = version_satisfies(found, wanted)
        if ok is None:
            return CheckResult(item["id"], WARN, f"cannot compare {found.strip()!r} with {wanted!r}")
        found = found.strip()
        return _outcome(item, ok, f"{tool} {found} satisfies {wanted}",
                        f"{tool} {found} does not satisfy {wanted}")
    return CheckResult(item["id"], WARN, f"unknown check kind {kind!r}")


def run_doctor(lock_path: Path, host: Host | None = None) -> list[CheckResult]:
    data = lockfile.load(lock_path)
    if data is None:
        raise FileNotFoundError(f"No {lock_path.name} in {lock_path.resolve().parent}. "
                                "Run `phantom exorcise` first or pass --lock.")
    host = host or Host.current()
    return [check(item, host) for item in data["requirements"]]
