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
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai: (*failure(), 'AI disabled with --no-ai', []))
    result = runner.invoke(cli.app, ['haunt', '--no-ai'])
    assert result.exit_code == 1
    for text in ('step 1', "KeyError: 'DATABASE_URL'", 'env_var', 'rule_matched', 'not yet verified'):
        assert text in result.stdout


def test_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai: (*failure(), 'AI disabled with --no-ai', []))
    result = runner.invoke(cli.app, ['haunt', '--no-ai', '--json'])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data['finding']['name'] == 'DATABASE_URL'
    assert data['finding']['risk_flags'] == []


def test_success_and_infrastructure_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    result = RunResult('abc', 'image', 'linux/arm64', [], True)
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai: (result, None, None, []))
    assert runner.invoke(cli.app, ['haunt']).exit_code == 0
    result.passed = False
    result.infra_error = "Docker isn't running. Start Docker Desktop and retry."
    failed = runner.invoke(cli.app, ['haunt', '--json'])
    assert failed.exit_code == 2
    assert json.loads(failed.stdout)['run']['infra_error'] == result.infra_error


def test_inconclusive_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = failure()
    result.steps[0].timed_out = True
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai: (result, classify(result.steps[0]), None, []))
    assert runner.invoke(cli.app, ['haunt']).exit_code == 2


def test_bad_path_is_friendly(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ['haunt', str(tmp_path), '--json'])
    assert result.exit_code == 2
    assert json.loads(result.stdout)['status'] == 'inconclusive'
    assert 'Traceback' not in result.stdout


def test_no_ai_flag_reaches_haunt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    def fake(path: Path, rev: str, use_ai: bool) -> cli.HauntOutcome:
        calls.append(use_ai)
        return (*failure(), None, [])

    monkeypatch.setattr(cli, '_haunt', fake)
    runner.invoke(cli.app, ['haunt', '--no-ai'])
    runner.invoke(cli.app, ['haunt'])
    assert calls == [False, True]


def test_json_includes_ai_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai: (*failure(), 'AI explanation unavailable: down', []))
    data = json.loads(runner.invoke(cli.app, ['haunt', '--json']).stdout)
    assert data['ai_note'] == 'AI explanation unavailable: down'


@pytest.mark.parametrize(('note', 'label'), [('explained by gemma3:4b', 'Gemma explanation'),
                                             ('explained by qwen2.5-coder:14b', 'AI explanation (qwen2.5-coder:14b)')])
def test_explanation_names_the_model(monkeypatch: pytest.MonkeyPatch, note: str, label: str) -> None:
    result, finding = failure()
    finding.explanation = 'DATABASE_URL is not set.'
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai: (result, finding, note, []))
    assert label in runner.invoke(cli.app, ['haunt']).stdout


def test_risk_flags_warn_without_changing_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    result, finding = failure()
    flags = ['step 1: runs with root privileges']
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai: (result, finding, None, flags))
    output = runner.invoke(cli.app, ['haunt'])
    assert output.exit_code == 1 and 'warnings only' in output.stdout
    assert 'step 1: runs with root privileges' in output.stdout
    data = json.loads(runner.invoke(cli.app, ['haunt', '--json']).stdout)
    assert data['risk_flags'] == flags
