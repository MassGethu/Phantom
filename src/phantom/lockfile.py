"""assumptions.lock v1: verified requirements only, validated and merged by id."""

from pathlib import Path
import re
from typing import Any

import yaml

from phantom import __version__
from phantom.models import Cause, Finding, ReqType, Status

SCHEMA_VERSION = 1
LOCK_NAME = "assumptions.lock"
FIELDS = ("id", "type", "name", "evidence", "cause", "introduced_in", "fix",
          "verification", "status", "check")
PREFIXES = {ReqType.ENV_VAR: "env", ReqType.FILE: "file", ReqType.TOOL: "tool",
            ReqType.SERVICE: "service", ReqType.VERSION: "version", ReqType.MODULE: "module",
            ReqType.STEP_ORDER: "step", ReqType.UNKNOWN: "unknown"}
# NOTE: file_exists is beyond the brief's four kinds so verified file fixes stay checkable.
CHECKS = {ReqType.ENV_VAR: "env_set", ReqType.FILE: "file_exists", ReqType.TOOL: "which",
          ReqType.VERSION: "version", ReqType.SERVICE: "tcp"}
CHECK_KINDS = set(CHECKS.values())
VERIFIED = {Status.VERIFIED_FIX.value, Status.VERIFIED_TOGETHER.value}


class LockError(ValueError):
    """The lock file is unreadable or violates the v1 schema."""


def requirement_id(req_type: ReqType, name: str) -> str:
    return f"{PREFIXES[req_type]}.{name}"


def entry(finding: Finding, fix_command: str, command_hash: str, introduced_in: str | None) -> dict[str, Any]:
    """Build a lock entry; refuses anything verification did not prove."""
    if finding.status.value not in VERIFIED or not finding.name:
        raise LockError("Only verified findings with a name belong in assumptions.lock.")
    if finding.req_type not in CHECKS:
        raise LockError(f"No local check exists for {finding.req_type.value} requirements.")
    step = finding.step.step
    return {
        "id": requirement_id(finding.req_type, finding.name),
        "type": finding.req_type.value,
        "name": finding.name,
        "evidence": f"step {step.index} `{step.command}`: {finding.evidence}",
        "cause": finding.cause.value,
        "introduced_in": introduced_in,
        "fix": fix_command,
        "verification": {"command_hash": command_hash, "before": "fail", "after": "pass"},
        "status": finding.status.value,
        "check": {"kind": CHECKS[finding.req_type]},
    }


def _entry_errors(item: Any, position: int) -> list[str]:
    where = f"requirements[{position}]"
    if not isinstance(item, dict):
        return [f"{where} must be a mapping"]
    missing = [name for name in FIELDS if name not in item]
    if missing:
        return [f"{where} is missing {', '.join(missing)}"]
    # Type checks come first: YAML can put lists or mappings anywhere, and those are unhashable.
    wrong = [name for name in ("id", "type", "cause", "status") if not isinstance(item[name], str)]
    if wrong:
        return [f"{where}.{name} must be a string" for name in wrong]
    errors: list[str] = []
    try:
        req_type = ReqType(item["type"])
        if item["id"] != requirement_id(req_type, str(item["name"])):
            errors.append(f"{where}.id must be {requirement_id(req_type, str(item['name']))}")
    except ValueError:
        errors.append(f"{where}.type is not a known requirement type")
    for name in ("name", "evidence", "fix"):
        if not isinstance(item[name], str) or not item[name]:
            errors.append(f"{where}.{name} must be a non-empty string")
    if item["cause"] not in {cause.value for cause in Cause} - {Cause.NONE.value}:
        errors.append(f"{where}.cause must be rule_matched or evidence_supported")
    if item["introduced_in"] is not None and not (
            isinstance(item["introduced_in"], str) and re.fullmatch(r"[0-9a-f]{7,40}", item["introduced_in"])):
        errors.append(f"{where}.introduced_in must be a commit sha or null")
    check = item["verification"]
    if not (isinstance(check, dict) and check.get("before") == "fail" and check.get("after") == "pass"
            and isinstance(check.get("command_hash"), str)
            and re.fullmatch(r"[0-9a-f]{64}", check["command_hash"])):
        errors.append(f"{where}.verification must record command_hash, before: fail, after: pass")
    if item["status"] not in VERIFIED:
        errors.append(f"{where}.status must be verified_fix or verified_together")
    kind = item["check"].get("kind") if isinstance(item["check"], dict) else None
    if not isinstance(kind, str) or kind not in CHECK_KINDS:
        errors.append(f"{where}.check.kind must be one of {', '.join(sorted(CHECK_KINDS))}")
    return errors


def validate(data: Any) -> None:
    if not isinstance(data, dict):
        raise LockError("assumptions.lock must be a YAML mapping.")
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(data.get("generated_by"), str):
        errors.append("generated_by must be a string")
    environment = data.get("environment")
    if not (isinstance(environment, dict) and isinstance(environment.get("image"), str)
            and isinstance(environment.get("platform"), str)):
        errors.append("environment must record image and platform")
    requirements = data.get("requirements")
    if not isinstance(requirements, list):
        errors.append("requirements must be a list")
    else:
        for position, item in enumerate(requirements):
            errors.extend(_entry_errors(item, position))
        ids = [item["id"] for item in requirements if isinstance(item, dict) and isinstance(item.get("id"), str)]
        if len(ids) != len(set(ids)):
            errors.append("requirement ids must be unique")
    if errors:
        raise LockError("Invalid assumptions.lock: " + "; ".join(errors))


def _yaml_problem(error: yaml.YAMLError) -> str:
    """One line instead of PyYAML's multi-line excerpt."""
    problem = getattr(error, "problem", None) or "invalid YAML"
    mark = getattr(error, "problem_mark", None)
    return f"{problem} at line {mark.line + 1}" if mark else problem


def load(path: Path) -> dict[str, Any] | None:
    """Return the validated lock, None when absent; raise LockError when unusable."""
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise LockError(f"Cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise LockError(f"Cannot read {path}: {_yaml_problem(error)}") from error
    validate(data)
    return data


def merge(existing: dict[str, Any] | None, image: str, platform: str,
          entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace entries with matching ids in place and append new ones, keeping order stable."""
    requirements = list(existing["requirements"]) if existing else []
    positions = {item["id"]: index for index, item in enumerate(requirements)}
    for item in entries:
        if item["id"] in positions:
            requirements[positions[item["id"]]] = item
        else:
            positions[item["id"]] = len(requirements)
            requirements.append(item)
    # NOTE: the environment records the most recent verification run.
    data = {"schema_version": SCHEMA_VERSION, "generated_by": f"phantom {__version__}",
            "environment": {"image": image, "platform": platform}, "requirements": requirements}
    validate(data)
    return data


def dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
