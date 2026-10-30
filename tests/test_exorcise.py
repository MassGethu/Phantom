from pathlib import Path
import runpy
import subprocess
from typing import Any

import pytest
from typer.testing import CliRunner

from ghostdev import cli, config, exorcise, lockfile, render
from ghostdev.bisect import run_bisect
from ghostdev.exorcise import DirtyReadmeError
from ghostdev.models import RunResult, Step, StepResult


def fake_run(repo: Path, sha: str, steps: list[Step]) -> RunResult:
    """The demo app needs DATABASE_URL at init; nothing here touches Docker."""
    results: list[StepResult] = []
    exported = False
    for step in steps:
        exported = exported or step.command.startswith('export DATABASE_URL=')
        if step.command == 'python app.py init' and not exported:
            results.append(StepResult(step, 1, "Traceback\nKeyError: 'DATABASE_URL'\n"))
            return RunResult(sha, 'python@sha256:abc', 'linux/arm64', results, False)
        results.append(StepResult(step, 0, ''))
    return RunResult(sha, 'python@sha256:abc', 'linux/arm64', results, True)


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[str]]:
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    repo = tmp_path / 'haunted-todo'
    return repo, runpy.run_path('scripts/make_haunted_todo.py')['generate'](repo)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.rstrip()


def test_plan_and_apply_without_committing(demo: tuple[Path, list[str]]) -> None:
    repo, shas = demo
    exorcism = exorcise.plan(repo, run=fake_run, use_ai=False)
    assert '+export DATABASE_URL=sqlite:///./todo.db' in exorcism.diff
    assert (repo / 'README.md').read_text() == exorcism.original
    written = exorcise.apply(exorcism)
    assert written == [repo / 'README.md', repo / 'assumptions.lock']
    data = lockfile.load(repo / 'assumptions.lock')
    entry = data['requirements'][0]
    assert entry['id'] == 'env.DATABASE_URL' and entry['status'] == 'verified_fix'
    assert entry['fix'] == 'export DATABASE_URL=sqlite:///./todo.db'
    assert entry['introduced_in'] is None
    assert git(repo, 'rev-parse', 'HEAD') == shas[-1]
    assert git(repo, 'status', '--porcelain').splitlines() == [' M README.md', '?? assumptions.lock']


def test_refuses_uncommitted_readme(demo: tuple[Path, list[str]]) -> None:
    repo, _ = demo
    (repo / 'README.md').write_text((repo / 'README.md').read_text() + 'local edit\n')
    with pytest.raises(DirtyReadmeError, match='uncommitted'):
        exorcise.plan(repo, run=fake_run, use_ai=False)


def test_readme_edited_during_verification_is_not_overwritten(demo: tuple[Path, list[str]]) -> None:
    repo, _ = demo
    exorcism = exorcise.plan(repo, run=fake_run, use_ai=False)
    (repo / 'README.md').write_text('edited meanwhile\n')
    with pytest.raises(DirtyReadmeError):
        exorcise.apply(exorcism)
    assert (repo / 'README.md').read_text() == 'edited meanwhile\n'


def test_introduced_in_comes_from_cached_bisect(demo: tuple[Path, list[str]]) -> None:
    repo, shas = demo
    run_bisect(repo, run=fake_run_for(shas), env=lambda: ('img@sha256:a', 'linux/arm64'), use_ai=False)
    exorcism = exorcise.plan(repo, run=fake_run, use_ai=False)
    assert exorcism.lock['requirements'][0]['introduced_in'] == shas[3][:7]


def fake_run_for(shas: list[str]) -> Any:
    def run(repo: Path, sha: str, steps: list[Step]) -> RunResult:
        if shas.index(sha) < 3:
            return RunResult(sha, 'img', 'p', [StepResult(s, 0, '') for s in steps], True)
        return fake_run(repo, sha, steps)
    return run


def test_existing_lock_entries_are_kept(demo: tuple[Path, list[str]]) -> None:
    repo, _ = demo
    exorcise.apply(exorcise.plan(repo, run=fake_run, use_ai=False))
    data = lockfile.load(repo / 'assumptions.lock')
    other = {**data['requirements'][0], 'id': 'env.OTHER', 'name': 'OTHER'}
    data['requirements'].append(other)
    (repo / 'assumptions.lock').write_text(lockfile.dump(data))
    git(repo, 'checkout', 'README.md')
    exorcism = exorcise.plan(repo, run=fake_run, use_ai=False)
    assert [item['id'] for item in exorcism.lock['requirements']] == ['env.DATABASE_URL', 'env.OTHER']


def test_invalid_existing_lock_stops_before_writing(demo: tuple[Path, list[str]]) -> None:
    repo, _ = demo
    (repo / 'assumptions.lock').write_text('schema_version: 9\n')
    with pytest.raises(lockfile.LockError):
        exorcise.plan(repo, run=fake_run, use_ai=False)


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render.console, 'width', 160)


@pytest.fixture
def cli_demo(demo: tuple[Path, list[str]], monkeypatch: pytest.MonkeyPatch) -> Path:
    real = exorcise.plan
    monkeypatch.setattr(cli, 'exorcise_plan', lambda path, rev, use_ai: real(path, rev, run=fake_run, use_ai=use_ai))
    return demo[0]


def test_cli_declined_writes_nothing(cli_demo: Path) -> None:
    result = CliRunner().invoke(cli.app, ['exorcise', str(cli_demo), '--no-ai'], input='n\n')
    assert result.exit_code == 0, result.stdout
    for text in ('Apply these changes?', 'No files changed.', 'Fix verified',
                 'export DATABASE_URL'):
        assert text in result.stdout
    assert not (cli_demo / 'assumptions.lock').exists()
    assert git(cli_demo, 'status', '--porcelain') == ''


def test_cli_confirmed_and_apply_flag(cli_demo: Path) -> None:
    confirmed = CliRunner().invoke(cli.app, ['exorcise', str(cli_demo), '--no-ai'], input='y\n')
    assert confirmed.exit_code == 0 and 'never commits' in confirmed.stdout
    assert (cli_demo / 'assumptions.lock').exists()
    git(cli_demo, 'checkout', 'README.md')
    flagged = CliRunner().invoke(cli.app, ['exorcise', str(cli_demo), '--no-ai', '--apply'])
    assert flagged.exit_code == 0 and 'Apply these changes?' not in flagged.stdout


def test_cli_unverified_exits_1(demo: tuple[Path, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = demo

    def tool_missing(repo: Path, sha: str, steps: list[Step]) -> RunResult:
        return RunResult(sha, 'img', 'p', [StepResult(steps[0], 127, 'bash: poetry: command not found\n')], False)

    real = exorcise.plan
    monkeypatch.setattr(cli, 'exorcise_plan', lambda path, rev, use_ai: real(path, rev, run=tool_missing, use_ai=use_ai))
    result = CliRunner().invoke(cli.app, ['exorcise', str(repo), '--apply'])
    assert result.exit_code == 1 and 'needs a human' in result.stdout
    assert git(repo, 'status', '--porcelain') == ''
