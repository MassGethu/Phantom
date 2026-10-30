from pathlib import Path
import runpy
import shutil
import subprocess

import pytest

from ghostdev.models import Step
from ghostdev.parse import extract_steps
from ghostdev.runner import resolve_sha, run_steps

pytestmark = pytest.mark.docker


@pytest.fixture(scope='module', autouse=True)
def docker_available() -> None:
    if not shutil.which('docker'):
        pytest.skip('Docker executable unavailable')
    try:
        check = subprocess.run(['docker', 'info'], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip('Docker daemon unavailable')
    if check.returncode:
        pytest.skip('Docker daemon unavailable')


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


def test_timeout_cleanup(demo: Path) -> None:
    result = run_steps(demo, resolve_sha(demo), [Step(1, 'echo ready; sleep 30', 1)], timeout=1)
    assert result.first_failure.timed_out
    assert result.first_failure.exit_code is None
    assert 'ready' in result.first_failure.output


def test_no_containers_left() -> None:
    result = subprocess.run(['docker', 'ps', '-aq', '--filter', 'name=ghost-'],
                            capture_output=True, text=True, check=True)
    assert not result.stdout.strip()
