from pathlib import Path
import runpy

import pytest

from phantom.models import Status
from phantom.parse import committed_readme
from phantom.runner import run_steps
from phantom.verify import verify_readme

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures('docker_available')]


def test_demo_fix_fails_before_and_passes_after(tmp_path: Path) -> None:
    repo = tmp_path / 'haunted-todo'
    sha = runpy.run_path('scripts/make_haunted_todo.py')['generate'](repo)[-1]
    outcome = verify_readme(repo, sha, committed_readme(repo, sha)[1], run_steps)
    assert outcome.before.first_failure.step.command == 'python app.py init'
    assert outcome.after.passed and len(outcome.after.steps) == 4
    assert [(f.name, f.status) for f in outcome.verified] == [('DATABASE_URL', Status.VERIFIED_FIX)]
    assert 'export DATABASE_URL=sqlite:///./todo.db\npython app.py init' in outcome.patched_text
