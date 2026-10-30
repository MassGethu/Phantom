"""Warning-only flags for risky README commands; they never block a run."""

import re

from phantom.models import Step

SECRET_NAME = r"[A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)[A-Za-z0-9_]*"
# Values that are obviously stand-ins rather than real credentials.
PLACEHOLDER = re.compile(
    r"""^(?:|<[^>]*>|\$\{?\w+\}?|\.\.\.|x+|\*+|changeme|change[-_]?me|placeholder|dummy|example|test|"""
    r"""your[-_]?\w*|replace[-_]?\w*|todo)$""",
    re.IGNORECASE,
)
PATTERNS = (
    (re.compile(r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b"),
     "pipes a download straight into a shell"),
    (re.compile(r"(?:^|[;&|]\s*|\s)sudo\b"), "runs with root privileges"),
    (re.compile(r"\bchmod\s+(?:-\w+\s+)*0?777\b"), "makes files writable by everyone"),
    (re.compile(r"\bnpm\s+(?:i|install)\b(?=.*\s(?:-g|--global)\b)"), "installs a global npm package"),
    (re.compile(r"--insecure\b|\bcurl\b(?=.*\s-k\b)"), "disables TLS certificate checks"),
)
PIP_INSTALL = re.compile(r"\bpip3?\s+install\s+(?P<args>[^;&|]*)")
SECRET_EXPORT = re.compile(rf"\bexport\s+(?P<name>{SECRET_NAME})=(?P<value>\S*)")


def _unpinned_pip(command: str) -> bool:
    for match in PIP_INSTALL.finditer(command):
        args = match["args"].split()
        if {"-r", "-e", "--requirement", "--editable"} & set(args):
            continue
        packages = [arg for arg in args if not arg.startswith("-") and arg != "."]
        if any("==" not in package for package in packages):
            return True
    return False


def _real_secret(command: str) -> str | None:
    for match in SECRET_EXPORT.finditer(command):
        if not PLACEHOLDER.fullmatch(match["value"].strip("'\"")):
            return match["name"]
    return None


def reasons(command: str) -> list[str]:
    found = [reason for pattern, reason in PATTERNS if pattern.search(command)]
    if _unpinned_pip(command):
        found.append("installs a pip package without pinning a version")
    secret = _real_secret(command)
    if secret:
        # Name the variable only; never echo the value.
        found.append(f"exports {secret} with what looks like a real value")
    return found


def flag_steps(steps: list[Step]) -> list[str]:
    return [f"step {step.index}: {reason}" for step in steps for reason in reasons(step.command)]
