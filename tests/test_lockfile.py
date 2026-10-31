from pathlib import Path

import pytest

from phantom import lockfile
from phantom.lockfile import LockError
from phantom.models import Cause, Finding, Fix, ReqType, Status, Step, StepResult

HASH = 'a' * 64


def finding(name: str = 'DATABASE_URL', status: Status = Status.VERIFIED_FIX) -> Finding:
    return Finding(StepResult(Step(1, 'python app.py init', 7), 1, f"KeyError: '{name}'"),
                   status, ReqType.ENV_VAR, name, f"KeyError: '{name}'", Cause.RULE_MATCHED,
                   fix=Fix(ReqType.ENV_VAR, name, 'x'))


def lock(*names: str) -> dict:
    entries = [lockfile.entry(finding(name), f'export {name}=x', HASH, None) for name in names]
    return lockfile.merge(None, 'python:3.12-slim@sha256:abc', 'linux/arm64', entries)


def test_entry_matches_v1_shape() -> None:
    item = lockfile.entry(finding(), 'export DATABASE_URL=sqlite:///./todo.db', HASH, 'a124636')
    assert list(item) == list(lockfile.FIELDS)
    assert item['id'] == 'env.DATABASE_URL'
    assert item['evidence'] == "step 1 `python app.py init`: KeyError: 'DATABASE_URL'"
    assert item['verification'] == {'command_hash': HASH, 'before': 'fail', 'after': 'pass'}
    assert item['check'] == {'kind': 'env_set'}


def test_only_verified_findings_enter_the_lock() -> None:
    with pytest.raises(LockError):
        lockfile.entry(finding(status=Status.HYPOTHESIS), 'export A=x', HASH, None)


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('status', 'hypothesis', 'status'), ('cause', 'none', 'cause'),
    ('verification', {'command_hash': HASH, 'before': 'pass', 'after': 'pass'}, 'verification'),
    ('check', {'kind': 'ping'}, 'check.kind'), ('id', 'env.OTHER', 'id'),
    ('introduced_in', 'not-a-sha', 'introduced_in'), ('fix', '', 'fix'),
])
def test_validate_rejects_bad_entries(field: str, value: object, message: str) -> None:
    data = lock('A')
    data['requirements'][0][field] = value
    with pytest.raises(LockError, match=message):
        lockfile.validate(data)


def test_validate_requires_every_field_and_header() -> None:
    data = lock('A')
    del data['requirements'][0]['introduced_in']
    with pytest.raises(LockError, match='missing introduced_in'):
        lockfile.validate(data)
    with pytest.raises(LockError, match='schema_version'):
        lockfile.validate({**lock('A'), 'schema_version': 2})
    with pytest.raises(LockError, match='unique'):
        lockfile.validate({**lock('A'), 'requirements': lock('A', 'A')['requirements'] * 2})


def test_merge_replaces_by_id_and_appends_in_order() -> None:
    existing = lock('A', 'B')
    updated = lockfile.entry(finding('A'), 'export A=new', HASH, 'a124636')
    merged = lockfile.merge(existing, 'img@sha256:new', 'linux/arm64',
                            [updated, lockfile.entry(finding('C'), 'export C=x', HASH, None)])
    assert [item['id'] for item in merged['requirements']] == ['env.A', 'env.B', 'env.C']
    assert merged['requirements'][0]['fix'] == 'export A=new'
    assert merged['environment']['image'] == 'img@sha256:new'


def test_dump_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / 'assumptions.lock'
    path.write_text(lockfile.dump(lock('A')))
    text = path.read_text()
    assert text.startswith('schema_version: 1\ngenerated_by: phantom 0.1.0\n')
    assert text.index('id: env.A') < text.index('type: env_var') < text.index('check:')
    assert lockfile.load(path) == lock('A')
    assert lockfile.load(tmp_path / 'missing.lock') is None
    path.write_text('requirements: [unclosed')
    with pytest.raises(LockError, match='Cannot read'):
        lockfile.load(path)


def test_unreadable_locks_raise_lock_error_with_one_line(tmp_path: Path) -> None:
    path = tmp_path / 'assumptions.lock'
    path.write_bytes(b'\xff\xfe\x00bad')
    with pytest.raises(LockError, match='Cannot read'):
        lockfile.load(path)
    path.write_text('# title\n```bash\nls\n```\n')
    with pytest.raises(LockError, match='line 2') as raised:
        lockfile.load(path)
    assert '\n' not in str(raised.value)


@pytest.mark.parametrize('field', ['id', 'type', 'cause', 'status'])
@pytest.mark.parametrize('value', [['verified_fix'], {'a': 1}, 5, None])
def test_wrong_field_types_raise_lock_error_not_type_error(field: str, value: object) -> None:
    data = lock('A')
    data['requirements'][0][field] = value
    with pytest.raises(LockError, match=f'{field} must be a string'):
        lockfile.validate(data)


@pytest.mark.parametrize('check', [{'kind': ['env_set']}, {'kind': {'a': 1}}, ['env_set']])
def test_unhashable_check_kind_raises_lock_error(check: object) -> None:
    data = lock('A')
    data['requirements'][0]['check'] = check
    with pytest.raises(LockError, match='check.kind'):
        lockfile.validate(data)


def test_status_list_in_a_file_gives_readable_error(tmp_path: Path) -> None:
    path = tmp_path / 'assumptions.lock'
    path.write_text(lockfile.dump(lock('A')).replace('status: verified_fix', 'status: [verified_fix]'))
    with pytest.raises(LockError, match='status must be a string'):
        lockfile.load(path)
