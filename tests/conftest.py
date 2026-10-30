import shutil
import subprocess

import pytest


@pytest.fixture(scope='session')
def docker_available() -> None:
    """Skip, never pass, Docker tests when the daemon is unavailable."""
    if not shutil.which('docker'):
        pytest.skip('Docker executable unavailable')
    try:
        check = subprocess.run(['docker', 'info'], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip('Docker daemon unavailable')
    if check.returncode:
        pytest.skip('Docker daemon unavailable')
