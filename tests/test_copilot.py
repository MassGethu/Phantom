from pathlib import Path

import pytest
import yaml

from phantom.copilot import WORKFLOW_PATH, python_version, render_workflow

COMMANDS = ['export DATABASE_URL=sqlite:///./todo.db', 'python app.py init',
            "python app.py add 'buy milk: now'", 'cat <<EOF > x.cfg\n  key: "#value"\nEOF']


def test_run_block_round_trips_commands_exactly() -> None:
    data = yaml.safe_load(render_workflow(COMMANDS, 'python:3.12-slim'))
    job = data['jobs']['copilot-setup-steps']
    assert job['permissions'] == {'contents': 'read'}
    assert job['steps'][1]['with'] == {'python-version': '3.12'}
    assert job['steps'][-1]['run'] == '\n'.join(COMMANDS) + '\n'
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
