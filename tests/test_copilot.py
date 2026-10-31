from pathlib import Path
import subprocess

import pytest
import yaml

from phantom import config
from phantom.copilot import SHELL, STEP_GUARD, WORKFLOW_PATH, python_version, render_workflow

COMMANDS = ['export DATABASE_URL=sqlite:///./todo.db', 'python app.py init',
            "python app.py add 'buy milk: now'", 'cat <<EOF > x.cfg\n  key: "#value"\nEOF']


def test_run_block_round_trips_commands_exactly() -> None:
    data = yaml.safe_load(render_workflow(COMMANDS, 'python:3.12-slim'))
    job = data['jobs']['copilot-setup-steps']
    assert job['permissions'] == {'contents': 'read'}
    assert job['steps'][1]['with'] == {'python-version': '3.12'}
    run = job['steps'][-1]['run']
    plumbing = {STEP_GUARD, '__rc=$?'}
    assert [line for line in run.splitlines() if line not in plumbing] == '\n'.join(COMMANDS).splitlines()
    assert data[True]['push']['paths'] == [str(WORKFLOW_PATH)]  # PyYAML reads the `on` key as True


def test_non_python_image_skips_setup_python() -> None:
    steps = yaml.safe_load(render_workflow(['node -v'], 'node:20'))['jobs']['copilot-setup-steps']['steps']
    assert [step.get('uses') for step in steps] == ['actions/checkout@v4', None]


@pytest.mark.parametrize(('image', 'version'), [('python:3.12-slim', '3.12'),
                                                ('docker.io/library/python:3.11', '3.11'),
                                                ('python@sha256:abc', None), ('ubuntu:24.04', None)])
def test_python_version(image: str, version: str | None) -> None:
    assert python_version(image) == version


def test_no_commands_is_an_error() -> None:
    with pytest.raises(ValueError):
        render_workflow([])


def run_like_github(tmp_path: Path, commands: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute the run block the way Actions does: write it to a file and pass it as {0}."""
    step = yaml.safe_load(render_workflow(commands, 'node:20'))['jobs']['copilot-setup-steps']['steps'][-1]
    script = tmp_path / 'step.sh'
    script.write_text(step['run'])
    argv = [str(script) if part == '{0}' else part for part in step['shell'].split()]
    return subprocess.run(argv, capture_output=True, text=True, cwd=tmp_path)


def test_shell_has_no_errexit_or_pipefail_like_the_verifier(tmp_path: Path) -> None:
    assert SHELL == 'bash --noprofile --norc {0}'
    result = run_like_github(tmp_path, ['false; echo recovered', 'false | true'])
    assert result.returncode == 0 and 'recovered' in result.stdout


def test_first_failing_step_stops_the_workflow_like_the_verifier(tmp_path: Path) -> None:
    result = run_like_github(tmp_path, ['echo one', 'exit_code() { return 3; }; exit_code', 'echo never'])
    assert result.returncode == 3 and 'one' in result.stdout and 'never' not in result.stdout


def test_server_step_does_not_hang_the_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, 'SERVER_GRACE', 1)
    result = run_like_github(tmp_path, ['nodemon() { sleep 3; }', 'nodemon', 'echo after'])
    assert result.returncode == 0 and 'after' in result.stdout
    assert 'phantom-started' not in result.stdout


def test_in_repo_steps_cd_to_the_checkout(tmp_path: Path) -> None:
    run = yaml.safe_load(render_workflow(['cd /tmp', 'pwd'], 'node:20', frozenset({2})))
    lines = run['jobs']['copilot-setup-steps']['steps'][-1]['run'].splitlines()
    assert lines.index('cd "$GITHUB_WORKSPACE" || exit 125') == lines.index('pwd') - 1
