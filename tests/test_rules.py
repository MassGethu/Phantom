from pathlib import Path
import runpy

import pytest

from phantom.models import Cause, ReqType, Status, Step, StepResult
from phantom.rules import (classify, default_fix, safe_example_value, validate_ai_output,
                            validate_evidence, validate_fix, validate_version)


def result(output: str, timed_out: bool = False) -> StepResult:
    return StepResult(Step(1, 'python app.py', 7), None if timed_out else 1, output, timed_out)


@pytest.mark.parametrize(('output', 'kind', 'name'), [
    ("KeyError: 'DATABASE_URL'", ReqType.ENV_VAR, 'DATABASE_URL'),
    ("os.environ['lower']\nKeyError: 'lower'", ReqType.ENV_VAR, 'lower'),
    ("getenv('lower')\nKeyError: 'lower'", ReqType.ENV_VAR, 'lower'),
    ('bash: line 1: poetry: command not found', ReqType.TOOL, 'poetry'),
    ('/bin/sh: 1: poetry: not found', ReqType.TOOL, 'poetry'),
    ("ModuleNotFoundError: No module named 'redis.client'", ReqType.MODULE, 'redis.client'),
    ('Connection refused at localhost:5432', ReqType.SERVICE, '5432'),
    ('Connection refused on port 6379', ReqType.SERVICE, '6379'),
    ("FileNotFoundError: [Errno 2] No such file or directory: 'config.yml'", ReqType.FILE, 'config.yml'),
    ('cat: config.yml: No such file or directory', ReqType.FILE, 'config.yml'),
])
def test_rule_patterns(output: str, kind: ReqType, name: str) -> None:
    finding = classify(result(output))
    assert (finding.req_type, finding.name) == (kind, name)
    assert finding.status == Status.HYPOTHESIS
    assert finding.cause == Cause.RULE_MATCHED
    assert finding.evidence in output.splitlines()


SELFLOCK_JAVA_ERROR = "ERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH."


@pytest.mark.parametrize(('output', 'name'), [
    (SELFLOCK_JAVA_ERROR, 'java'),
    ('JAVA_HOME is not set\nNo "java" command could be found in your PATH.', 'java'),
    ("No 'node' command could be found in your PATH.", 'node'),
    ('No `ruby` command was found on PATH.', 'ruby'),
    ("ERROR: Could not find executable 'cmake'", 'cmake'),
    ('Unable to locate the required binary "ffmpeg" on PATH', 'ffmpeg'),
    ('Cannot find program python3.12', 'python3.12'),
    ("Required executable 'protoc' is missing", 'protoc'),
    ('command pnpm is not installed', 'pnpm'),
    ('Program "rustc" was not found in PATH', 'rustc'),
])
def test_generic_missing_tools(output: str, name: str) -> None:
    finding = classify(result(output))
    assert (finding.req_type, finding.name) == (ReqType.TOOL, name)
    assert finding.status == Status.HYPOTHESIS
    assert finding.cause == Cause.RULE_MATCHED
    assert finding.evidence in output.splitlines()
    assert finding.fix is None  # Recognition does not invent installation commands.


@pytest.mark.parametrize(('output', 'name'), [
    ('ERROR: JAVA_HOME is not set', 'JAVA_HOME'),
    ("Environment variable 'ANDROID_HOME' is not set", 'ANDROID_HOME'),
    ('env var DATABASE_URL is missing', 'DATABASE_URL'),
    ('Environment variable "custom_path" is required', 'custom_path'),
])
def test_generic_missing_environment_variables(output: str, name: str) -> None:
    finding = classify(result(output))
    assert (finding.req_type, finding.name) == (ReqType.ENV_VAR, name)
    assert finding.cause == Cause.RULE_MATCHED


@pytest.mark.parametrize('output', [
    'Build target not found', 'No matching distribution found for java',
    'Could not find config file config.yml', 'Executable is not found',
    'Could not find executable in PATH', 'java exited with status 1',
    'JAVA_HOME is set but invalid', 'Environment variable FOO is set',
])
def test_generic_patterns_do_not_guess_without_a_missing_requirement(output: str) -> None:
    finding = classify(result(output))
    assert finding.req_type == ReqType.UNKNOWN
    assert finding.status == Status.OBSERVED


def test_generic_patterns_preserve_infrastructure_and_timeout_priority() -> None:
    assert classify(result(SELFLOCK_JAVA_ERROR + '\nCould not resolve host')).status == Status.INCONCLUSIVE
    assert classify(result(SELFLOCK_JAVA_ERROR, timed_out=True)).status == Status.INCONCLUSIVE


@pytest.mark.parametrize('output', ['Could not resolve host', 'Temporary failure in name resolution',
    'Connection timed out', 'pull access denied', 'TLS handshake failure', 'SSL: CERTIFICATE failure',
    'No space left on device'])
def test_infra_priority(output: str) -> None:
    finding = classify(result("KeyError: 'URL'\n" + output))
    assert finding.status == Status.INCONCLUSIVE
    assert finding.req_type == ReqType.UNKNOWN


def test_timeout_and_unknown() -> None:
    assert classify(result('', True)).status == Status.INCONCLUSIVE
    finding = classify(result("KeyError: 'ordinary_key'\ncleanup complete"))
    assert finding.status == Status.OBSERVED
    assert finding.evidence == "KeyError: 'ordinary_key'"


def test_committed_example_lookup(tmp_path: Path) -> None:
    module = runpy.run_path('scripts/make_haunted_todo.py')
    repo = tmp_path / 'demo'
    shas = module['generate'](repo)
    (repo / '.env.example').write_text('DATABASE_URL=working-tree-value\n')
    assert default_fix(ReqType.ENV_VAR, 'DATABASE_URL', repo, shas[-1]).example_value == 'sqlite:///./todo.db'
    assert default_fix(ReqType.ENV_VAR, 'DATABASE_URL', repo, shas[2]).example_value == 'changeme'
    assert default_fix(ReqType.TOOL, 'poetry') is None
    assert default_fix(ReqType.FILE, 'missing', repo, shas[-1]) is None
    assert default_fix(ReqType.FILE, '.env', repo, shas[-1]).name == '.env'


@pytest.mark.parametrize('secret', ['sk-abcdef', 'ghp_abcdef', 'AKIA12345678', 'xoxb-abcdef',
                                     'xoxp-abcdef', '-----BEGIN PRIVATE KEY-----', 'a' * 33])
def test_secret_rejection(secret: str) -> None:
    assert safe_example_value(secret) == 'changeme'
    assert safe_example_value('sqlite:///./todo.db') == 'sqlite:///./todo.db'


def test_evidence_and_rule_priority() -> None:
    step = result("KeyError: 'DATABASE_URL'")
    payload = {'type': 'env_var', 'name': 'DATABASE_URL', 'evidence_line': 'invented',
               'explanation': 'Set DATABASE_URL.', 'fix': None}
    assert not validate_evidence(payload, step)
    original = classify(step)
    assert validate_ai_output(payload, original).explanation is None
    unknown = classify(result('DATABASE_URL error'))
    assert validate_ai_output(payload, unknown).status == Status.UNSUPPORTED
    payload['evidence_line'] = step.output
    validated = validate_ai_output(payload, original)
    assert validated.explanation == 'Set DATABASE_URL.'
    assert validated.status == Status.HYPOTHESIS
    payload['type'] = 'tool'
    assert validate_ai_output(payload, original).req_type == ReqType.ENV_VAR
    assert validate_ai_output(payload, original).explanation is None


def test_version_backing() -> None:
    assert not validate_version('python', {'.nvmrc': '20'})
    assert validate_version('python', {'.python-version': '3.12'})
    assert validate_version('python', {'pyproject.toml': '[project]\nrequires-python = ">=3.11"'})
    assert validate_version('node', {'.nvmrc': '20'})
    assert validate_version('node', {'package.json': '{"engines":{"node":">=20"}}'})
    assert validate_version('ruby', {'.tool-versions': 'ruby 3.2'})
    assert not validate_version('python', {'pyproject.toml': 'invalid['})


def test_structured_fix_only() -> None:
    assert validate_fix('export URL=value') is None
    assert validate_fix({'type': 'shell', 'name': 'URL', 'example_value': 'value'}) is None
    assert validate_fix({'type': 'env_var', 'name': 'URL; touch /tmp/no', 'example_value': 'x'}) is None
    fix = validate_fix({'type': 'env_var', 'name': 'URL', 'example_value': 'sk-secret'})
    assert fix.example_value == 'changeme'
    assert validate_fix({'type': 'file', 'name': '../outside', 'example_value': None}) is None
    assert validate_fix({'type': 'env_var', 'name': 'URL', 'example_value': 42}) is None
    assert validate_ai_output({'type': 'verified_fix'}, classify(result('error'))).status == Status.UNSUPPORTED
