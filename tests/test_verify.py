from pathlib import Path
import runpy

import pytest

from phantom.models import Fix, ReqType, RunResult, Status, Step, StepResult
from phantom.parse import extract_steps_from_text
from phantom.verify import (Candidate, PatchError, apply_fixes, original_step, patch_readme,
                             render_fix, verify_fix, verify_readme)

README = '''# Demo

## Setup
```bash
git clone https://example.com/demo.git
cd demo
python app.py init
python app.py add "buy milk"
python app.py list
```
'''
CONSOLE = '''```console
$ python app.py init
output line
$ python app.py list
```
'''
REPO = SHA = None  # set by the demo fixture; the rules read committed example files


@pytest.fixture(scope='module', autouse=True)
def demo(tmp_path_factory: pytest.TempPathFactory) -> None:
    global REPO, SHA
    REPO = tmp_path_factory.mktemp('demo') / 'haunted-todo'
    SHA = runpy.run_path('scripts/make_haunted_todo.py')['generate'](REPO)[-1]
ENV_FIX = Fix(ReqType.ENV_VAR, 'DATABASE_URL', 'sqlite:///./todo.db')


class FakeRunner:
    """Runs steps by rule: a command needing NAME fails unless an earlier export set it."""

    def __init__(self, needs: dict[str, list[str]] | None = None, infra_after: int | None = None) -> None:
        self.needs = {'python app.py init': ['DATABASE_URL']} if needs is None else needs
        self.infra_after = infra_after
        self.runs: list[list[str]] = []

    def __call__(self, repo: Path, sha: str, steps: list[Step]) -> RunResult:
        assert sha == SHA, 'verification must reuse the same commit'
        self.runs.append([step.command for step in steps])
        if self.infra_after is not None and len(self.runs) > self.infra_after:
            return RunResult(sha, 'img', 'linux/arm64', [], False, 'Container preparation failed')
        exported: set[str] = set()
        results: list[StepResult] = []
        for step in steps:
            if step.command.startswith('export '):
                exported.add(step.command.split()[1].split('=')[0])
            missing = [name for name in self.needs.get(step.command, []) if name not in exported]
            if missing:
                results.append(StepResult(step, 1, f"Traceback\nKeyError: '{missing[0]}'\n"))
                return RunResult(sha, 'img', 'linux/arm64', results, False)
            results.append(StepResult(step, 0, ''))
        return RunResult(sha, 'img', 'linux/arm64', results, True)


def test_templates_quote_values() -> None:
    assert render_fix(ENV_FIX) == 'export DATABASE_URL=sqlite:///./todo.db'
    assert render_fix(Fix(ReqType.ENV_VAR, 'A', 'x; rm -rf /')) == "export A='x; rm -rf /'"
    assert render_fix(Fix(ReqType.FILE, 'my config.yml')) == "cp 'my config.yml.example' 'my config.yml'"
    for unsafe in (Fix(ReqType.ENV_VAR, 'A;B', 'x'), Fix(ReqType.FILE, '../x'),
                   Fix(ReqType.FILE, '/etc/passwd'), Fix(ReqType.FILE, '-rf'), Fix(ReqType.TOOL, 'poetry')):
        with pytest.raises(PatchError):
            render_fix(unsafe)


def test_patch_inserts_inside_block_before_failing_step() -> None:
    steps = extract_steps_from_text(README)
    patched = patch_readme(README, steps[0], ENV_FIX)
    lines = patched.splitlines()
    assert lines[6:9] == ['# required: DATABASE_URL (added by phantom)',
                          'export DATABASE_URL=sqlite:///./todo.db', 'python app.py init']
    assert lines[-1] == '```'
    reparsed = extract_steps_from_text(patched)
    assert [s.command for s in reparsed][:2] == ['export DATABASE_URL=sqlite:///./todo.db', 'python app.py init']
    assert reparsed[1].source_line == steps[0].source_line + 2


def test_console_patch_keeps_prompt_and_parses() -> None:
    steps = extract_steps_from_text(CONSOLE)
    patched = patch_readme(CONSOLE, steps[1], Fix(ReqType.ENV_VAR, 'TOKEN', 'changeme'))
    assert '$ export TOKEN=changeme' in patched
    assert [s.command for s in extract_steps_from_text(patched)] == [
        'python app.py init', 'export TOKEN=changeme', 'python app.py list']


def test_patch_preserves_crlf_and_indentation() -> None:
    text = '- Setup:\r\n  ```sh\r\n  make run\r\n  ```\r\n'
    patched = patch_readme(text, extract_steps_from_text(text)[0], Fix(ReqType.ENV_VAR, 'A', 'b'))
    assert '  export A=b\r\n  make run\r\n' in patched


def test_patch_refuses_wrong_line() -> None:
    with pytest.raises(PatchError):
        patch_readme(README, Step(1, 'python app.py init', 2), ENV_FIX)
    with pytest.raises(PatchError):
        patch_readme(README, Step(1, 'python app.py init', 99), ENV_FIX)


def test_two_fixes_for_one_step_and_mapping() -> None:
    steps = extract_steps_from_text(README)
    candidates = [Candidate(steps[0], ENV_FIX, None), Candidate(steps[0], Fix(ReqType.ENV_VAR, 'B', 'x'), None),
                  Candidate(steps[2], Fix(ReqType.ENV_VAR, 'C', 'y'), None)]
    patched = extract_steps_from_text(apply_fixes(README, candidates))
    assert [s.command for s in patched] == [
        'export DATABASE_URL=sqlite:///./todo.db', 'export B=x', 'python app.py init',
        'python app.py add "buy milk"', 'export C=y', 'python app.py list']
    assert original_step(patched[2], steps, candidates) == steps[0]
    assert original_step(patched[5], steps, candidates) == steps[2]
    assert original_step(patched[0], steps, candidates) is None


def test_verify_fix_before_fail_after_pass() -> None:
    patched = patch_readme(README, extract_steps_from_text(README)[0], ENV_FIX)
    runner = FakeRunner()
    status, after = verify_fix(REPO, SHA, README, patched, runner)
    assert status == Status.VERIFIED_FIX and after.passed
    assert len(runner.runs) == 2 and runner.runs[0] != runner.runs[1]
    assert verify_fix(REPO, SHA, patched, patched, FakeRunner())[0] is None


def test_single_round_verified_fix() -> None:
    runner = FakeRunner()
    outcome = verify_readme(REPO, SHA, README, runner)
    assert [f.status for f in outcome.verified] == [Status.VERIFIED_FIX]
    assert outcome.verified[0].step.step.index == 1
    assert 'export DATABASE_URL=sqlite:///./todo.db' in outcome.patched_text
    assert outcome.command_hash and not outcome.unverified
    assert len(runner.runs) == 2


def test_nothing_to_fix() -> None:
    outcome = verify_readme(REPO, SHA, README, FakeRunner(needs={}))
    assert outcome.nothing_to_fix and not outcome.verified


def test_multi_round_needs_both_fixes_together() -> None:
    runner = FakeRunner(needs={'python app.py init': ['DATABASE_URL'], 'python app.py list': ['PAGE_SIZE']})
    outcome = verify_readme(REPO, SHA, README, runner)
    assert [(f.name, f.status) for f in outcome.verified] == [
        ('DATABASE_URL', Status.VERIFIED_TOGETHER), ('PAGE_SIZE', Status.VERIFIED_TOGETHER)]
    assert [f.step.step.index for f in outcome.verified] == [1, 3]
    assert outcome.after.passed
    # before, round 1, round 2, then PAGE_SIZE alone (R6).
    assert len(runner.runs) == 4


def test_partial_improvement_is_not_verified() -> None:
    runner = FakeRunner(needs={'python app.py init': ['DATABASE_URL'],
                               'python app.py add "buy milk"': ['A1', 'A2', 'A3']})
    outcome = verify_readme(REPO, SHA, README, runner, max_rounds=2)
    assert not outcome.verified and outcome.patched_text is None
    assert [f.name for f in outcome.unverified] == ['DATABASE_URL', 'A1']
    assert all(f.status == Status.HYPOTHESIS for f in outcome.unverified)


def test_requirement_without_template_needs_a_human() -> None:
    def runner(repo: Path, sha: str, steps: list[Step]) -> RunResult:
        failed = StepResult(steps[0], 127, 'bash: line 1: poetry: command not found\n')
        return RunResult(sha, 'img', 'p', [failed], False)

    outcome = verify_readme(REPO, SHA, README, runner)
    assert not outcome.verified
    assert [(f.req_type, f.name) for f in outcome.unverified] == [(ReqType.TOOL, 'poetry')]


def test_infrastructure_during_verification_is_inconclusive() -> None:
    outcome = verify_readme(REPO, SHA, README, FakeRunner(infra_after=1))
    assert outcome.inconclusive and not outcome.verified
