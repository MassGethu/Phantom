import json

from phantom.models import Cause, Finding, Fix, ReqType, RunResult, Status, Step, StepResult


def test_run_results() -> None:
    success = StepResult(Step(1, "true", 3), 0, "")
    failure = StepResult(Step(2, "false", 4), 1, "failed")
    result = RunResult("abc", "image", "linux/arm64", [success, failure], False)
    assert result.first_failure is failure
    assert not result.passed
    passing = RunResult("abc", "image", "linux/arm64", [success], True)
    assert passing.passed and passing.first_failure is None
    timeout = StepResult(Step(1, "sleep 99", 1), None, "", True)
    assert RunResult("a", "i", "p", [timeout], False).first_failure is timeout


def test_finding_json_round_trip() -> None:
    finding = Finding(StepResult(Step(1, "python app.py", 5), 1, "KeyError: 'URL'"),
                      Status.HYPOTHESIS, ReqType.ENV_VAR, "URL", "KeyError: 'URL'",
                      Cause.RULE_MATCHED, fix=Fix(ReqType.ENV_VAR, "URL", "changeme"))
    data = json.loads(json.dumps(finding.to_dict()))
    assert data["req_type"] == "env_var"
    assert Finding.from_dict(data) == finding
