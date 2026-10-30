from pathlib import Path
import runpy
import subprocess

import pytest

from phantom import config
from phantom.bisect import run_bisect

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures('docker_available')]


def test_real_bisect_finds_c4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    repo = tmp_path / 'haunted-todo'
    shas = runpy.run_path('scripts/make_haunted_todo.py')['generate'](repo)
    result = run_bisect(repo, use_ai=False)
    assert result.culprit == shas[3]
    again = run_bisect(repo, use_ai=False)
    assert again.culprit == shas[3] and all(probe.cached for probe in again.probes)
    left = subprocess.run(['docker', 'ps', '-aq', '--filter', 'name=phantom-'],
                          capture_output=True, text=True, check=True)
    assert not left.stdout.strip()
