"""Shared execution and diagnosis records."""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from typing import Any


class Status(str, Enum):
    OBSERVED = "observed"
    INCONCLUSIVE = "inconclusive"
    HYPOTHESIS = "hypothesis"
    UNSUPPORTED = "unsupported"
    VERIFIED_FIX = "verified_fix"
    VERIFIED_TOGETHER = "verified_together"


class ReqType(str, Enum):
    ENV_VAR = "env_var"
    TOOL = "tool"
    MODULE = "module"
    SERVICE = "service"
    FILE = "file"
    VERSION = "version"
    STEP_ORDER = "step_order"
    UNKNOWN = "unknown"


class Cause(str, Enum):
    RULE_MATCHED = "rule_matched"
    EVIDENCE_SUPPORTED = "evidence_supported"
    NONE = "none"


@dataclass
class Step:
    index: int
    command: str
    source_line: int


@dataclass
class StepResult:
    step: Step
    exit_code: int | None
    output: str
    timed_out: bool = False


@dataclass
class RunResult:
    sha: str
    image: str
    platform: str
    steps: list[StepResult]
    passed: bool
    infra_error: str | None = None

    @property
    def first_failure(self) -> StepResult | None:
        return next((step for step in self.steps
                     if step.exit_code != 0 or step.timed_out), None)


@dataclass
class Fix:
    type: ReqType
    name: str
    example_value: str | None = None


@dataclass
class Finding:
    step: StepResult
    status: Status
    req_type: ReqType
    name: str | None
    evidence: str
    cause: Cause
    explanation: str | None = None
    fix: Fix | None = None
    risk_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        values = dict(data)
        step = dict(values["step"])
        step["step"] = Step(**step["step"])
        values["step"] = StepResult(**step)
        values["status"] = Status(values["status"])
        values["req_type"] = ReqType(values["req_type"])
        values["cause"] = Cause(values["cause"])
        if values.get("fix"):
            values["fix"] = Fix(**{**values["fix"], "type": ReqType(values["fix"]["type"])})
        return cls(**values)
