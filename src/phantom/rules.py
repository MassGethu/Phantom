"""Rules decide the requirement; optional model output must pass pure validators."""

from collections.abc import Mapping
from dataclasses import replace
import json
from pathlib import Path
import re
import subprocess
import tomllib
from typing import Any

from phantom import config
from phantom.models import Cause, Finding, Fix, ReqType, Status, StepResult

INFRA_PATTERNS = (
    r"Could not resolve host", r"Temporary failure in name resolution",
    r"Connection timed out", r"pull access denied", r"\bTLS\b",
    r"SSL: CERTIFICATE", r"CERTIFICATE_VERIFY_FAILED", r"No space left on device",
)
MIN_EVIDENCE_CHARS = 4
MAX_EXPLANATION_SENTENCES = 2
SECRET_PATTERN = re.compile(
    r"sk-[\w-]+|ghp_[\w]+|AKIA[\w]+|xox[bp]-[\w-]+|-----BEGIN|[A-Za-z0-9+/=_-]{33,}"
)
# Match diagnostic grammar rather than a list of known runtimes or package names.
# Keep identifiers bounded so prose such as "build target not found" is not a tool.
TOOL_NAME = r"(?!(?:is|was|not|in|on|the|a)\b)[A-Za-z0-9_./+-]+"
MISSING_TOOL_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    rf"\bno\s+['\"`]?(?P<name>{TOOL_NAME})['\"`]?\s+command\s+(?:(?:could be|was)\s+)?found\b",
    rf"\b(?:could not|cannot|unable to)\s+(?:find|locate)\s+(?:the\s+)?(?:required\s+)?"
    rf"(?:command|executable|program|binary)\s+['\"`]?(?P<name>{TOOL_NAME})['\"`]?(?=\s|[,:;.!]|$)",
    rf"\b(?:command|executable|program|binary)\s+['\"`]?(?P<name>{TOOL_NAME})['\"`]?\s+"
    rf"(?:(?:is|was)\s+)?(?:not found|missing|not installed)\b",
))
MISSING_ENV_PATTERNS = (
    re.compile(r"\b(?:environment variable|env var)\s+['\"`]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
               r"['\"`]?\s+(?:is\s+)?(?:not set|unset|missing|required)\b", re.I),
    re.compile(r"^(?:ERROR:\s*)?(?P<name>[A-Z][A-Z0-9_]*)\s+is\s+(?:not set|unset)\b"),
)


def safe_example_value(value: str | None) -> str:
    if not value or SECRET_PATTERN.search(value) or '\n' in value or '\r' in value or '\x00' in value:
        return "changeme"
    return value


def repo_file(repo: Path, sha: str, name: str) -> str | None:
    result = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{name}"],
                            capture_output=True, text=True, timeout=config.GIT_TIMEOUT)
    if result.returncode:
        # An absent example file is normal; other Git errors should reach the CLI boundary.
        if "does not exist" in result.stderr or "exists on disk, but not in" in result.stderr:
            return None
        raise ValueError(f"Cannot read committed example file: {result.stderr.strip()}")
    return result.stdout


def default_fix(req_type: ReqType, name: str, repo: Path | None = None,
                sha: str | None = None) -> Fix | None:
    if req_type == ReqType.ENV_VAR:
        value = "changeme"
        if repo is not None and sha is not None:
            for filename in (".env.example", ".env.sample", "example.env"):
                text = repo_file(repo, sha, filename)
                if text is None:
                    continue
                match = re.search(r"^\s*(?:export\s+)?" + re.escape(name) + r"\s*=\s*(.*?)\s*$", text, re.M)
                if match:
                    value = match[1].strip().strip("\"'")
                    break
        return Fix(req_type, name, safe_example_value(value))
    if req_type == ReqType.FILE and repo is not None and sha is not None:
        if repo_file(repo, sha, f"{name}.example") is not None:
            return Fix(req_type, name)
    return None


def _finding(result: StepResult, req_type: ReqType, name: str, line: str,
             repo: Path | None, sha: str | None) -> Finding:
    return Finding(result, Status.HYPOTHESIS, req_type, name, line, Cause.RULE_MATCHED,
                   fix=default_fix(req_type, name, repo, sha))


def classify(result: StepResult, repo: Path | None = None, sha: str | None = None) -> Finding:
    lines = [line for line in result.output.splitlines() if line.strip()]
    for line in lines:
        if any(re.search(pattern, line, re.I) for pattern in INFRA_PATTERNS):
            return Finding(result, Status.INCONCLUSIVE, ReqType.UNKNOWN, None, line, Cause.NONE)
    if result.timed_out:
        return Finding(result, Status.INCONCLUSIVE, ReqType.UNKNOWN, None,
                       lines[-1] if lines else "", Cause.NONE)
    for line in lines:
        match = re.search(r"KeyError:\s*['\"](\w+)['\"]", line)
        if match and (re.fullmatch(r"[A-Z][A-Z0-9_]*", match[1]) or
                      re.search(r"os\.environ|\bgetenv\b", result.output)):
            return _finding(result, ReqType.ENV_VAR, match[1], line, repo, sha)
    # Check the more specific exceptions before generic shell 'not found' messages.
    patterns = [
        (ReqType.MODULE, r"ModuleNotFoundError: No module named ['\"]([\w.]+)['\"]"),
        (ReqType.FILE, r"FileNotFoundError:.*?['\"]([^'\"]+)['\"]"),
        # python: can't open file '/repo/hello.py': [Errno 2] No such file or directory
        (ReqType.FILE, r"can't open file ['\"](?:/repo/)?([^'\"]+)['\"]:\s*\[Errno 2\] No such file or directory"),
        # uvicorn/gunicorn: Could not import module "main".
        (ReqType.MODULE, r"Could not import module ['\"]([\w.]+)['\"]"),
        (ReqType.FILE, r"(?:^|:\s)([^:\n]+): No such file or directory"),
        (ReqType.TOOL, r"(?:^|:\s)([^\s:]+): (?:command )?not found(?:\s|$)"),
    ]
    for req_type, pattern in patterns:
        for line in lines:
            match = re.search(pattern, line)
            if match:
                return _finding(result, req_type, match[1].strip(), line, repo, sha)
    # Prefer an explicitly missing executable over a related configuration variable
    # (for example, Gradle reports both JAVA_HOME and a missing java command).
    for req_type, generic_patterns in ((ReqType.TOOL, MISSING_TOOL_PATTERNS),
                                       (ReqType.ENV_VAR, MISSING_ENV_PATTERNS)):
        for line in lines:
            for pattern in generic_patterns:
                match = pattern.search(line)
                if match:
                    return _finding(result, req_type, match['name'], line, repo, sha)
    if re.search(r"Connection refused", result.output, re.I):
        for line in lines:
            port = re.search(r":(\d{2,5})\b|\bport[ =:]+(\d{2,5})\b|\(['\"](?:localhost|127\.0\.0\.1)['\"],\s*(\d{2,5})\)", line, re.I)
            if port:
                name = next(group for group in port.groups() if group)
                return _finding(result, ReqType.SERVICE, name, line, repo, sha)
    error_lines = [line for line in lines if re.search(r"error|failed|exception|fatal", line, re.I)]
    evidence = (error_lines or lines or [""])[-1]
    return Finding(result, Status.OBSERVED, ReqType.UNKNOWN, None, evidence, Cause.NONE)


def mentions(name: str, text: str) -> bool:
    """True when name appears as a whole identifier, not inside a longer word."""
    return bool(name) and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text) is not None


def validate_evidence(payload: Mapping[str, Any], result: StepResult) -> bool:
    """R2: the evidence must be a real fragment of one output line that names the requirement."""
    evidence, name = payload.get("evidence_line"), payload.get("name")
    if not isinstance(evidence, str) or not isinstance(name, str):
        return False
    evidence, name = evidence.strip(), name.strip()
    if len(evidence) < MIN_EVIDENCE_CHARS or "\n" in evidence:
        return False
    return (any(evidence in line for line in result.output.splitlines())
            and mentions(name, evidence))


def clean_explanation(text: Any) -> str | None:
    """Keep at most two sentences of plain model prose; anything else is dropped."""
    if not isinstance(text, str) or not text.strip():
        return None
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    short = " ".join(sentences[:MAX_EXPLANATION_SENTENCES])
    return short if len(short) <= config.MAX_EXPLANATION_CHARS else None


def validate_version(name: str, repo_files: Mapping[str, str]) -> bool:
    """Require a declaration for the named runtime, not an unrelated version file."""
    tool = name.lower()
    if tool in {"python", "python3"}:
        if repo_files.get(".python-version", "").strip():
            return True
        try:
            if tomllib.loads(repo_files.get("pyproject.toml", "")).get("project", {}).get("requires-python"):
                return True
        except tomllib.TOMLDecodeError:
            pass
    if tool in {"node", "nodejs", "npm"}:
        if tool != "npm" and repo_files.get(".nvmrc", "").strip():
            return True
        try:
            engines = json.loads(repo_files.get("package.json", "{}")).get("engines", {})
            if isinstance(engines, dict) and engines.get("node" if tool == "nodejs" else tool):
                return True
        except (ValueError, AttributeError):
            pass
    return bool(re.search(r"^" + re.escape(tool) + r"\s+\S+", repo_files.get(".tool-versions", ""), re.M))


def validate_fix(payload: Any) -> Fix | None:
    if not isinstance(payload, dict) or set(payload) != {"type", "name", "example_value"}:
        return None
    try:
        req_type = ReqType(payload["type"])
    except (ValueError, TypeError):
        return None
    name, value = payload["name"], payload["example_value"]
    if not isinstance(name, str) or not name or (value is not None and not isinstance(value, str)):
        return None
    if req_type == ReqType.ENV_VAR and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return None
    if req_type not in {ReqType.ENV_VAR, ReqType.FILE}:
        return None
    if req_type == ReqType.FILE and (name.startswith(("/", "-")) or ".." in Path(name).parts or '\x00' in name):
        return None
    return Fix(req_type, name, safe_example_value(value) if req_type == ReqType.ENV_VAR else None)


def validate_ai_output(payload: Any, finding: Finding,
                       repo_files: Mapping[str, str] | None = None) -> Finding:
    """Return a copy; a model can never assign verified status or override a rule."""
    supported = isinstance(payload, dict) and validate_evidence(payload, finding.step)
    if supported:
        payload = {**payload, "name": payload["name"].strip(),
                   "evidence_line": payload["evidence_line"].strip()}
    try:
        req_type = ReqType(payload.get("type")) if isinstance(payload, dict) else ReqType.UNKNOWN
    except (TypeError, ValueError):
        supported, req_type = False, ReqType.UNKNOWN
    if supported and req_type == ReqType.VERSION:
        supported = validate_version(payload["name"], repo_files or {})
    if finding.status == Status.INCONCLUSIVE:
        return finding
    rule_matched = finding.cause == Cause.RULE_MATCHED
    if rule_matched and (not supported or req_type != finding.req_type or payload["name"] != finding.name):
        return replace(finding, explanation=None)
    if not supported:
        return replace(finding, status=Status.UNSUPPORTED, explanation=None, fix=None)
    explanation = clean_explanation(payload.get("explanation"))
    if explanation is None:
        return replace(finding, explanation=None)
    fix = validate_fix(payload.get("fix"))
    if fix and (fix.type != req_type or fix.name != payload["name"]):
        fix = None
    return replace(finding, status=Status.HYPOTHESIS, req_type=req_type,
                   name=payload["name"], evidence=payload["evidence_line"],
                   cause=Cause.RULE_MATCHED if rule_matched else Cause.EVIDENCE_SUPPORTED,
                   explanation=explanation, fix=finding.fix if rule_matched else fix)
