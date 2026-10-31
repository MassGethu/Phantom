import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from phantom import cli
from phantom.models import Finding, RunResult, Step, StepResult
from phantom.rules import classify

runner = CliRunner()


def failure() -> tuple[RunResult, Finding]:
    step = StepResult(Step(1, 'python app.py init', 7), 1, "KeyError: 'DATABASE_URL'\n")
    result = RunResult('abc', 'image@sha256:123', 'linux/arm64', [step], False)
    return result, classify(step)


def test_haunt_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (*failure(), 'AI disabled with --no-ai', []))
    result = runner.invoke(cli.app, ['haunt', '--no-ai'])
    assert result.exit_code == 1
    for text in ('step 1', "KeyError: 'DATABASE_URL'", 'env_var', 'rule_matched', 'not yet verified'):
        assert text in result.stdout


def test_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (*failure(), 'AI disabled with --no-ai', []))
    result = runner.invoke(cli.app, ['haunt', '--no-ai', '--json'])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data['finding']['name'] == 'DATABASE_URL'
    assert data['finding']['risk_flags'] == []


def test_success_and_infrastructure_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    result = RunResult('abc', 'image', 'linux/arm64', [], True)
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (result, None, None, []))
    assert runner.invoke(cli.app, ['haunt']).exit_code == 0
    result.passed = False
    result.infra_error = "Docker isn't running. Start Docker Desktop and retry."
    failed = runner.invoke(cli.app, ['haunt', '--json'])
    assert failed.exit_code == 2
    assert json.loads(failed.stdout)['run']['infra_error'] == result.infra_error


def test_inconclusive_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = failure()
    result.steps[0].timed_out = True
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (result, classify(result.steps[0]), None, []))
    assert runner.invoke(cli.app, ['haunt']).exit_code == 2


def test_bad_path_is_friendly(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ['haunt', str(tmp_path), '--json'])
    assert result.exit_code == 2
    assert json.loads(result.stdout)['status'] == 'inconclusive'
    assert 'Traceback' not in result.stdout


def test_no_ai_flag_reaches_haunt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    def fake(path: Path, rev: str, use_ai: bool, on_step: object = None) -> cli.HauntOutcome:
        calls.append(use_ai)
        return (*failure(), None, [])

    monkeypatch.setattr(cli, '_haunt', fake)
    runner.invoke(cli.app, ['haunt', '--no-ai'])
    runner.invoke(cli.app, ['haunt'])
    assert calls == [False, True]


@pytest.mark.parametrize(('output', 'tool'), [
    ("ERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH.", 'java'),
    ("Could not find executable 'cmake'", 'cmake'),
    ('No "node" command could be found in your PATH.', 'node'),
])
def test_haunt_no_ai_classifies_generic_tools(monkeypatch: pytest.MonkeyPatch,
                                           output: str, tool: str) -> None:
    # Exercise the real haunt/classification path, replacing only Git and Docker I/O.
    monkeypatch.setattr(cli, 'resolve_sha', lambda *args: 'abc')
    monkeypatch.setattr(cli, 'committed_readme', lambda *args: ('README.md', '```bash\n./gradlew assembleDebug\n```'))

    def run(repo: Path, sha: str, steps: list[Step], on_step: object = None) -> RunResult:
        return RunResult(sha, 'image', 'linux/arm64', [StepResult(steps[0], 1, output)], False)

    def unexpected_ai(*args: object) -> None:
        pytest.fail('--no-ai must not call AI diagnosis')

    monkeypatch.setattr(cli, 'run_steps', run)
    monkeypatch.setattr(cli, 'diagnose', unexpected_ai)
    invocation = runner.invoke(cli.app, ['haunt', '--no-ai', '--json'])
    assert invocation.exit_code == 1
    data = json.loads(invocation.stdout)
    assert data['finding']['req_type'] == 'tool'
    assert data['finding']['name'] == tool
    assert data['finding']['cause'] == 'rule_matched'
    assert data['finding']['fix'] is None
    assert data['ai_note'] == 'AI disabled with --no-ai'


def test_json_includes_ai_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (*failure(), 'AI explanation unavailable: down', []))
    data = json.loads(runner.invoke(cli.app, ['haunt', '--json']).stdout)
    assert data['ai_note'] == 'AI explanation unavailable: down'


@pytest.mark.parametrize(('note', 'label'), [('explained by gemma3:4b', 'Gemma explanation'),
                                             ('explained by qwen2.5-coder:14b', 'AI explanation (qwen2.5-coder:14b)')])
def test_explanation_names_the_model(monkeypatch: pytest.MonkeyPatch, note: str, label: str) -> None:
    result, finding = failure()
    finding.explanation = 'DATABASE_URL is not set.'
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (result, finding, note, []))
    assert label in runner.invoke(cli.app, ['haunt']).stdout


def test_risk_flags_warn_without_changing_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    result, finding = failure()
    flags = ['step 1: runs with root privileges']
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (result, finding, None, flags))
    output = runner.invoke(cli.app, ['haunt'])
    assert output.exit_code == 1 and 'warnings only' in output.stdout
    assert 'step 1: runs with root privileges' in output.stdout
    data = json.loads(runner.invoke(cli.app, ['haunt', '--json']).stdout)
    assert data['risk_flags'] == flags


def test_haunt_hints_the_next_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (*failure(), None, []))
    output = runner.invoke(cli.app, ['haunt', 'my repo']).stdout
    assert "→ Verify and patch it: phantom exorcise 'my repo'" in output
    result, finding = failure()
    finding.fix = None
    monkeypatch.setattr(cli, '_haunt', lambda path, rev, use_ai, on_step=None: (result, finding, None, []))
    assert '→ Find the commit that introduced it: phantom bisect .' in runner.invoke(cli.app, ['haunt']).stdout


def test_haunt_passes_a_progress_callback_only_outside_json(monkeypatch: pytest.MonkeyPatch) -> None:
    callbacks: list[object] = []

    def fake(path: Path, rev: str, use_ai: bool, on_step: object = None) -> cli.HauntOutcome:
        callbacks.append(on_step)
        return (*failure(), None, [])

    monkeypatch.setattr(cli, '_haunt', fake)
    runner.invoke(cli.app, ['haunt'])
    runner.invoke(cli.app, ['haunt', '--json'])
    assert callable(callbacks[0]) and callbacks[1] is None
