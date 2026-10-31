from pathlib import Path
import runpy
import subprocess

import pytest

from phantom import config
from phantom.models import Step
from phantom.parse import extract_steps
from phantom.runner import resolve_sha, run_steps

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures('docker_available')]


@pytest.fixture(scope='module')
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    module = runpy.run_path('scripts/make_haunted_todo.py')
    repo = tmp_path_factory.mktemp('demo') / 'haunted-todo'
    module['generate'](repo)
    return repo


def test_head_fails(demo: Path) -> None:
    result = run_steps(demo, resolve_sha(demo), extract_steps(demo))
    assert result.infra_error is None, result.infra_error
    assert not result.passed
    assert len(result.steps) == 1
    assert "KeyError: 'DATABASE_URL'" in result.first_failure.output
    assert result.image.count('@sha256:') == 1
    assert result.platform in {'linux/arm64', 'linux/amd64'}


def test_c3_passes(demo: Path) -> None:
    result = run_steps(demo, resolve_sha(demo, 'HEAD~5'), extract_steps(demo))
    assert result.infra_error is None, result.infra_error
    assert result.passed
    assert len(result.steps) == 3


def test_single_shell_session(demo: Path) -> None:
    steps = [Step(1, 'export FOO=bar', 1), Step(2, 'test "$FOO" = bar', 2)]
    result = run_steps(demo, resolve_sha(demo), steps)
    assert result.passed, result


def test_output_without_newline(demo: Path) -> None:
    result = run_steps(demo, resolve_sha(demo), [Step(1, 'printf hello', 1)])
    assert result.passed, result
    assert result.steps[0].output.strip() == 'hello'


def test_host_environment_not_forwarded(demo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('PHANTOM_TEST_SECRET', 'host-only-placeholder')
    result = run_steps(demo, resolve_sha(demo),
                       [Step(1, 'test -z "${PHANTOM_TEST_SECRET+x}"', 1)])
    assert result.passed, result


def test_timeout_cleanup(demo: Path) -> None:
    result = run_steps(demo, resolve_sha(demo), [Step(1, 'echo ready; sleep 30', 1)], timeout=1)
    assert result.first_failure.timed_out
    assert result.first_failure.exit_code is None
    assert 'ready' in result.first_failure.output


def test_no_containers_left() -> None:
    result = subprocess.run(['docker', 'ps', '-aq', '--filter', 'name=phantom-'],
                            capture_output=True, text=True, check=True)
    assert not result.stdout.strip()


def test_server_step_starts_and_later_steps_reach_it(demo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, 'SERVER_GRACE', 2)
    probe = 'python -c "import urllib.request as u; print(u.urlopen(\'http://127.0.0.1:8000\').status)"'
    steps = [Step(1, 'python -m http.server 8000', 1), Step(2, probe, 2)]
    result = run_steps(demo, resolve_sha(demo), steps)
    assert result.infra_error is None, result.infra_error
    assert result.passed and result.steps[0].started
    assert '200' in result.steps[1].output


def test_in_repo_step_starts_from_the_repo_root(demo: Path) -> None:
    steps = [Step(1, 'cd /tmp', 1), Step(2, 'pwd', 2, in_repo=True)]
    result = run_steps(demo, resolve_sha(demo), steps)
    assert result.passed and result.steps[1].output.strip() == '/repo'
