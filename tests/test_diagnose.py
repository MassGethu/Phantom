import io
import json
from pathlib import Path
import runpy
from typing import Any
import urllib.error

import pytest

from ghostdev import diagnose as diag
from ghostdev.diagnose import AIUnavailable, diagnose, explain_commit, generate
from ghostdev.models import Cause, ReqType, Status, Step, StepResult
from ghostdev.rules import classify, validate_evidence

STEPS = [Step(1, 'python app.py init', 7)]
OUTPUT = ('Traceback (most recent call last):\n  File "/repo/app.py", line 17, in main\n'
          "KeyError: 'DATABASE_URL'\n")


@pytest.fixture(scope='module')
def demo(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    repo = tmp_path_factory.mktemp('demo') / 'haunted-todo'
    shas = runpy.run_path('scripts/make_haunted_todo.py')['generate'](repo)
    return repo, shas[-1]


def answer(**overrides: Any) -> dict[str, Any]:
    payload = {'type': 'env_var', 'name': 'DATABASE_URL', 'evidence_line': "KeyError: 'DATABASE_URL'",
               'explanation': 'app.py reads DATABASE_URL from the environment. The README never sets it.',
               'fix': None}
    return {**payload, **overrides}


def fake(payload: Any) -> diag.GenerateFn:
    return lambda prompt, schema: ('gemma3:4b', payload)


def failing() -> StepResult:
    return StepResult(STEPS[0], 1, OUTPUT)


def test_validated_explanation_keeps_rule_decision(demo: tuple[Path, str]) -> None:
    repo, sha = demo
    rule = classify(failing(), repo, sha)
    finding, note = diagnose(rule, STEPS, repo, sha, fake(answer(fix={
        'type': 'env_var', 'name': 'DATABASE_URL', 'example_value': 'postgres://model'})))
    assert note == 'explained by gemma3:4b'
    assert finding.explanation.startswith('app.py reads DATABASE_URL')
    assert (finding.req_type, finding.name, finding.cause) == (ReqType.ENV_VAR, 'DATABASE_URL', Cause.RULE_MATCHED)
    assert finding.fix.example_value == 'sqlite:///./todo.db'


@pytest.mark.parametrize('payload', [
    answer(evidence_line='KeyError: DATABASE_URL is missing'),
    answer(name='SECRET_KEY', evidence_line="KeyError: 'DATABASE_URL'"),
    answer(type='tool'),
    answer(explanation=''),
    'not an object',
    None,
])
def test_rejected_answers_fall_back_to_rule(demo: tuple[Path, str], payload: Any) -> None:
    repo, sha = demo
    rule = classify(failing(), repo, sha)
    finding, note = diagnose(rule, STEPS, repo, sha, fake(payload))
    assert finding == rule
    assert note.startswith('AI explanation rejected')


def test_unavailable_model_keeps_rule_finding(demo: tuple[Path, str]) -> None:
    repo, sha = demo
    rule = classify(failing(), repo, sha)

    def down(prompt: str, schema: dict[str, Any]) -> Any:
        raise AIUnavailable('Ollama is not reachable at http://localhost:11434')

    assert diagnose(rule, STEPS, repo, sha, down) == (
        rule, 'AI explanation unavailable: Ollama is not reachable at http://localhost:11434')


def test_inconclusive_runs_never_ask_the_model(demo: tuple[Path, str]) -> None:
    repo, sha = demo
    timed_out = classify(StepResult(STEPS[0], None, 'partial', True))

    def explode(prompt: str, schema: dict[str, Any]) -> Any:
        raise AssertionError('model must not be called')

    assert diagnose(timed_out, STEPS, repo, sha, explode)[0] == timed_out


def test_evidence_supported_finding_without_rule(demo: tuple[Path, str]) -> None:
    repo, sha = demo
    output = 'Error: settings require APP_MODE before startup\n'
    observed = classify(StepResult(STEPS[0], 1, output))
    assert observed.status == Status.OBSERVED
    finding, note = diagnose(observed, STEPS, repo, sha, fake(answer(
        name='APP_MODE', evidence_line='settings require APP_MODE before startup',
        explanation='The app needs APP_MODE.',
        fix={'type': 'env_var', 'name': 'APP_MODE', 'example_value': 'ghp_realtoken'})))
    assert note == 'explained by gemma3:4b'
    assert (finding.status, finding.cause, finding.name) == (Status.HYPOTHESIS, Cause.EVIDENCE_SUPPORTED, 'APP_MODE')
    assert finding.fix.example_value == 'changeme'


def test_model_file_fix_requires_committed_example(demo: tuple[Path, str]) -> None:
    repo, sha = demo
    observed = classify(StepResult(STEPS[0], 1, 'Error: settings.toml is required\n'))
    payload = answer(type='file', name='settings.toml', evidence_line='settings.toml is required',
                     fix={'type': 'file', 'name': 'settings.toml', 'example_value': None})
    finding, _ = diagnose(observed, STEPS, repo, sha, fake(payload))
    assert finding.req_type == ReqType.FILE and finding.fix is None


def test_version_claim_needs_a_version_file(demo: tuple[Path, str]) -> None:
    repo, sha = demo
    observed = classify(StepResult(STEPS[0], 1, 'Error: python 3.13 required\n'))
    finding, _ = diagnose(observed, STEPS, repo, sha, fake(answer(
        type='version', name='python', evidence_line='python 3.13 required')))
    assert finding.status == Status.UNSUPPORTED and finding.explanation is None


def test_model_cannot_assign_status(demo: tuple[Path, str]) -> None:
    repo, sha = demo
    finding, _ = diagnose(classify(failing(), repo, sha), STEPS, repo, sha,
                          fake({**answer(), 'status': 'verified_fix'}))
    assert finding.status == Status.HYPOTHESIS


def test_long_explanations_are_trimmed(demo: tuple[Path, str]) -> None:
    repo, sha = demo
    finding, _ = diagnose(classify(failing(), repo, sha), STEPS, repo, sha,
                          fake(answer(explanation='One. Two. Three. Four.')))
    assert finding.explanation == 'One. Two.'


def test_evidence_must_be_a_meaningful_line_fragment() -> None:
    step = failing()
    assert not validate_evidence({'evidence_line': 'K', 'name': 'K'}, step)
    assert not validate_evidence({'evidence_line': "KeyError: 'DATABASE_URL'", 'name': 'URL'}, step)
    assert not validate_evidence({'evidence_line': "line 17, in main\nKeyError: 'DATABASE_URL'",
                                  'name': 'DATABASE_URL'}, step)
    assert validate_evidence({'evidence_line': " KeyError: 'DATABASE_URL' ", 'name': 'DATABASE_URL'}, step)


def test_prompt_contains_required_context() -> None:
    prompt = diag.build_prompt(classify(failing()), STEPS)
    for text in ('python app.py init', 'Exit code: 1', "KeyError: 'DATABASE_URL'",
                 'already classified this as env_var DATABASE_URL'):
        assert text in prompt


def test_explain_commit_requires_identifier_in_diff() -> None:
    finding = classify(failing())
    diff = '+    db_path = os.environ["DATABASE_URL"]\n'
    assert explain_commit(diff, finding, fake({'identifier': 'DATABASE_URL',
                                               'explanation': 'It reads DATABASE_URL.'})) == 'It reads DATABASE_URL.'
    assert explain_commit(diff, finding, fake({'identifier': 'OTHER', 'explanation': 'x'})) is None

    def explode(prompt: str, schema: dict[str, Any]) -> Any:
        raise AssertionError('model must not be called')

    assert explain_commit('+ unrelated\n', finding, explode) is None


class Reply(io.BytesIO):
    def __enter__(self) -> 'Reply':
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_generate_falls_back_to_second_model(monkeypatch: pytest.MonkeyPatch) -> None:
    models: list[str] = []

    def urlopen(request: Any, timeout: int) -> Reply:
        body = json.loads(request.data)
        models.append(body['model'])
        assert body['stream'] is False and body['options'] == {'temperature': 0}
        assert body['format'] == diag.FINDING_SCHEMA
        if len(models) == 1:
            raise urllib.error.HTTPError(request.full_url, 404, 'not found', None, None)
        return Reply(json.dumps({'response': json.dumps({'ok': True})}).encode())

    monkeypatch.setattr(diag.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(diag.config, 'MODEL', 'gemma3:4b')
    monkeypatch.setattr(diag.config, 'FALLBACK_MODEL', 'qwen2.5-coder:14b')
    assert generate('prompt', diag.FINDING_SCHEMA) == ('qwen2.5-coder:14b', {'ok': True})
    assert models == ['gemma3:4b', 'qwen2.5-coder:14b']


@pytest.mark.parametrize(('effect', 'message'), [
    (urllib.error.URLError('refused'), 'not reachable'),
    (TimeoutError(), 'did not answer'),
    (Reply(b'{"response": "not json"}'), 'valid JSON'),
    (Reply(b'{"unexpected": 1}'), 'valid JSON'),
    (urllib.error.HTTPError('u', 500, 'boom', None, None), 'HTTP 500'),
])
def test_generate_failures_become_unavailable(monkeypatch: pytest.MonkeyPatch,
                                              effect: Any, message: str) -> None:
    def urlopen(request: Any, timeout: int) -> Reply:
        if isinstance(effect, Exception):
            raise effect
        return effect

    monkeypatch.setattr(diag.urllib.request, 'urlopen', urlopen)
    with pytest.raises(AIUnavailable, match=message):
        generate('prompt', diag.FINDING_SCHEMA)


MALFORMED = [
    {}, [], 42, 'null', {'type': None}, {'type': ['env_var']}, answer(name=5), answer(name=None),
    answer(evidence_line=['x']), answer(evidence_line=None), answer(explanation=7),
    answer(explanation=['a']), answer(fix='export X=1'), answer(fix={'type': 9}),
    answer(fix={'type': 'env_var', 'name': ['x']}), answer(fix=[]), answer(type='verified_fix'),
]


@pytest.mark.parametrize('payload', MALFORMED)
def test_malformed_model_output_never_raises_or_verifies(demo: tuple[Path, str], payload: Any) -> None:
    repo, sha = demo
    unmatched = StepResult(STEPS[0], 1, 'boot failed\nconfig FOO_BAR missing\n')
    for finding in (classify(failing(), repo, sha), classify(unmatched, repo, sha)):
        result, note = diagnose(finding, STEPS, repo, sha, fake(payload))
        assert result.status not in (Status.VERIFIED_FIX, Status.VERIFIED_TOGETHER)
        assert isinstance(note, str)


@pytest.mark.parametrize('payload', MALFORMED + [{'identifier': 'DATABASE_URL', 'explanation': 3},
                                                 {'identifier': ['DATABASE_URL']}])
def test_malformed_commit_explanation_is_dropped(payload: Any) -> None:
    finding = classify(failing())
    diff = "+    url = os.environ['DATABASE_URL']\n"
    assert explain_commit(diff, finding, fake(payload)) is None
