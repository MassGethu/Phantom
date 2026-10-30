import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghostdev import cli
from ghostdev.models import Finding, RunResult, Step, StepResult
from ghostdev.rules import classify

runner = CliRunner()


def failure() -> tuple[RunResult, Finding]:
    step = StepResult(Step(1, 'python app.py init', 7), 1, "KeyError: 'DATABASE_URL'\n")
    result = RunResult('abc', 'image@sha256:123', 'linux/arm64', [step], False)
    return result, classify(step)


def test_haunt_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, '_haunt', lambda path, rev: failure())
    result = runner.invoke(cli.app, ['haunt', '--no-ai'])
    assert result.exit_code == 1
    for text in ('step 1', "KeyError: 'DATABASE_URL'", 'env_var', 'rule_matched', 'not yet verified'):
        assert text in result.stdout


def test_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, '_haunt', lambda path, rev: failure())
    result = runner.invoke(cli.app, ['haunt', '--no-ai', '--json'])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data['finding']['name'] == 'DATABASE_URL'
    assert data['finding']['risk_flags'] == []


def test_success_and_infrastructure_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    result = RunResult('abc', 'image', 'linux/arm64', [], True)
    monkeypatch.setattr(cli, '_haunt', lambda path, rev: (result, None))
    assert runner.invoke(cli.app, ['haunt']).exit_code == 0
    result.passed = False
    result.infra_error = "Docker isn't running. Start Docker Desktop and retry."
    failed = runner.invoke(cli.app, ['haunt', '--json'])
    assert failed.exit_code == 2
    assert json.loads(failed.stdout)['run']['infra_error'] == result.infra_error


def test_inconclusive_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = failure()
    result.steps[0].timed_out = True
    monkeypatch.setattr(cli, '_haunt', lambda path, rev: (result, classify(result.steps[0])))
    assert runner.invoke(cli.app, ['haunt']).exit_code == 2


def test_bad_path_is_friendly(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ['haunt', str(tmp_path), '--json'])
    assert result.exit_code == 2
    assert json.loads(result.stdout)['status'] == 'inconclusive'
    assert 'Traceback' not in result.stdout
