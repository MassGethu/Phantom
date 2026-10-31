from pathlib import Path
import socket

import pytest
from typer.testing import CliRunner

from phantom import cli, doctor, lockfile
from phantom.doctor import FAIL, PASS, WARN, Host, check, version_satisfies
from phantom.models import Cause, Finding, Fix, ReqType, Status, Step, StepResult

HASH = 'a' * 64


def item(kind: str, name: str = 'DATABASE_URL', **spec: object) -> dict:
    return {'id': f'x.{name}', 'name': name, 'fix': f'fix {name}', 'check': {'kind': kind, **spec}}


def host(tmp_path: Path, environ: dict | None = None, tools: dict | None = None) -> Host:
    tools = tools or {}
    return Host(environ or {}, tmp_path, lambda tool: f'/bin/{tool}' if tool in tools else None,
                lambda tool: tools[tool])


def test_env_set_passes_and_fails_with_hint(tmp_path: Path) -> None:
    assert check(item('env_set'), host(tmp_path, {'DATABASE_URL': 'x'})).state == PASS
    missing = check(item('env_set'), host(tmp_path))
    assert (missing.state, missing.detail, missing.hint) == (FAIL, 'DATABASE_URL is not set', 'fix DATABASE_URL')


def test_file_exists_relative_to_cwd(tmp_path: Path) -> None:
    assert check(item('file_exists', '.env'), host(tmp_path)).state == FAIL
    (tmp_path / '.env').write_text('A=1\n')
    assert check(item('file_exists', '.env'), host(tmp_path)).state == PASS


def test_which(tmp_path: Path) -> None:
    assert check(item('which', 'poetry'), host(tmp_path, tools={'poetry': ''})).state == PASS
    assert check(item('which', 'poetry'), host(tmp_path)).state == FAIL


def test_tcp_against_a_real_listener(tmp_path: Path) -> None:
    with socket.socket() as server:
        server.bind(('localhost', 0))
        server.listen()
        port = server.getsockname()[1]
        assert check(item('tcp', 'postgres', port=port), host(tmp_path)).state == PASS
    assert check(item('tcp', 'postgres', port=port), host(tmp_path)).state == FAIL
    assert check(item('tcp', 'postgres', port='abc'), host(tmp_path)).state == WARN


@pytest.mark.parametrize(('found', 'spec', 'expected'), [
    ('Python 3.12.4', '>=3.11', True), ('Python 3.10.1', '>=3.11', False),
    ('node v20.1.0', '20', True), ('node v18.0.0', '20', False),
    ('3.12.0', '==3.12.0', True), ('3.12.1', '>3.12', True),
    ('no digits', '>=1', None), ('3.12', '~=3', None),
])
def test_version_satisfies(found: str, spec: str, expected: bool | None) -> None:
    assert version_satisfies(found, spec) is expected


def test_version_check(tmp_path: Path) -> None:
    spec = item('version', 'python', tool='python', spec='>=3.11')
    assert check(spec, host(tmp_path, tools={'python': 'Python 3.12.4\n'})).state == PASS
    old = check(spec, host(tmp_path, tools={'python': 'Python 3.9.0\n'}))
    assert old.state == FAIL and 'does not satisfy >=3.11' in old.detail
    assert check(spec, host(tmp_path)).state == FAIL
    assert check(item('ping'), host(tmp_path)).state == WARN


def write_lock(path: Path) -> Path:
    finding = Finding(StepResult(Step(1, 'python app.py init', 7), 1, "KeyError: 'DATABASE_URL'"),
                      Status.VERIFIED_FIX, ReqType.ENV_VAR, 'DATABASE_URL', "KeyError: 'DATABASE_URL'",
                      Cause.RULE_MATCHED, fix=Fix(ReqType.ENV_VAR, 'DATABASE_URL', 'x'))
    data = lockfile.merge(None, 'img@sha256:a', 'linux/arm64',
                          [lockfile.entry(finding, 'export DATABASE_URL=sqlite:///./todo.db', HASH, None)])
    path.write_text(lockfile.dump(data))
    return path


def test_cli_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = write_lock(tmp_path / 'assumptions.lock')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    failed = CliRunner().invoke(cli.app, ['doctor', '--lock', str(lock)])
    assert failed.exit_code == 1, failed.stdout
    assert '❌ env.DATABASE_URL' in failed.stdout and 'hint: export DATABASE_URL' in failed.stdout
    assert '0 passed · 1 failed · 0 warnings' in failed.stdout and '→ Fix the failed checks' in failed.stdout
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///./todo.db')
    passed = CliRunner().invoke(cli.app, ['doctor', '--lock', str(lock)])
    assert passed.exit_code == 0 and '✅ env.DATABASE_URL' in passed.stdout
    assert '1 passed · 0 failed · 0 warnings' in passed.stdout and '→' not in passed.stdout
    assert 'sqlite' not in passed.stdout


def test_cli_missing_or_invalid_lock_exits_2(tmp_path: Path) -> None:
    missing = CliRunner().invoke(cli.app, ['doctor', '--lock', str(tmp_path / 'none.lock')])
    assert missing.exit_code == 2 and 'phantom exorcise' in missing.stdout
    (tmp_path / 'bad.lock').write_text('schema_version: 9\n')
    invalid = CliRunner().invoke(cli.app, ['doctor', '--lock', str(tmp_path / 'bad.lock')])
    assert invalid.exit_code == 2 and 'Traceback' not in invalid.stdout


def test_run_doctor_reads_every_requirement(tmp_path: Path) -> None:
    lock = write_lock(tmp_path / 'assumptions.lock')
    results = doctor.run_doctor(lock, host(tmp_path))
    assert [(r.id, r.state) for r in results] == [('env.DATABASE_URL', FAIL)]
