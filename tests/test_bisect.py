from pathlib import Path
import runpy
import shutil
import subprocess
from typing import Any

import pytest
from typer.testing import CliRunner

from phantom import bisect as bis, cli, config
from phantom.bisect import BoundaryError, InconclusiveError, matching_hunk, run_bisect
from phantom.models import RunResult, Step, StepResult

ENV = ('python@sha256:abc', 'linux/arm64')


@pytest.fixture(scope='module')
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    repo = tmp_path_factory.mktemp('demo') / 'haunted-todo'
    return repo, runpy.run_path('scripts/make_haunted_todo.py')['generate'](repo)


@pytest.fixture(autouse=True)
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    return tmp_path / 'cache'


class FakeRunner:
    """Commit index >= broken_from fails like the demo; indexes in skip hit infrastructure."""

    def __init__(self, shas: list[str], broken_from: int = 3, skip: set[int] | None = None) -> None:
        self.shas, self.broken_from, self.skip = shas, broken_from, skip or set()
        self.calls: list[str] = []

    def __call__(self, repo: Path, sha: str, steps: list[Step]) -> RunResult:
        self.calls.append(sha)
        index = self.shas.index(sha)
        if index in self.skip:
            return RunResult(sha, ENV[0], ENV[1], [], False, "Container preparation failed: network")
        if index >= self.broken_from:
            failed = StepResult(steps[0], 1, "Traceback\nKeyError: 'DATABASE_URL'\n")
            return RunResult(sha, ENV[0], ENV[1], [failed], False)
        return RunResult(sha, ENV[0], ENV[1], [StepResult(s, 0, '') for s in steps], True)


def no_ai(prompt: str, schema: dict[str, Any]) -> Any:
    raise AssertionError('AI must not be called')


def bisect(repo: Path, runner: FakeRunner, **kwargs: Any) -> bis.BisectResult:
    return run_bisect(repo, run=runner, env=lambda: ENV, generate_fn=no_ai, use_ai=False, **kwargs)


def test_finds_c4_and_reuses_cache(generated: tuple[Path, list[str]]) -> None:
    repo, shas = generated
    runner = FakeRunner(shas)
    result = bisect(repo, runner)
    assert result.culprit == shas[3]
    assert result.commit.subject == 'refactor: make database location configurable'
    assert result.commit.author == 'Haunted Maintainer <maintainer@example.com>'
    assert 'app.py' in result.commit.stat
    assert result.commit.hunk.startswith('diff --git a/app.py')
    assert 'os.environ["DATABASE_URL"]' in result.commit.hunk
    assert result.finding.name == 'DATABASE_URL'
    assert len(runner.calls) < len(shas)

    again = FakeRunner(shas)
    cached = bisect(repo, again)
    assert again.calls == [] and cached.culprit == shas[3]
    assert all(probe.cached for probe in cached.probes)


def test_cache_identity_includes_image_digest(generated: tuple[Path, list[str]]) -> None:
    repo, shas = generated
    bisect(repo, FakeRunner(shas))
    runner = FakeRunner(shas)
    run_bisect(repo, run=runner, env=lambda: ('python@sha256:other', ENV[1]), use_ai=False)
    assert runner.calls


def test_corrupt_cache_is_reported_and_rewritten(generated: tuple[Path, list[str]], cache_dir: Path) -> None:
    repo, shas = generated
    cache_dir.mkdir(parents=True)
    (cache_dir / 'results.json').write_text('{not json')
    result = bisect(repo, FakeRunner(shas))
    assert result.culprit == shas[3]
    assert 'Ignored unreadable cache' in result.cache_note
    assert bis.Cache(cache_dir / 'results.json').note is None


def test_bad_good_boundary_is_clear(generated: tuple[Path, list[str]]) -> None:
    repo, shas = generated
    with pytest.raises(BoundaryError, match='known-good commit also fails'):
        bisect(repo, FakeRunner(shas), good=shas[4])
    with pytest.raises(BoundaryError, match='--bad commit passes'):
        bisect(repo, FakeRunner(shas), bad=shas[2])
    with pytest.raises(BoundaryError, match='same commit'):
        bisect(repo, FakeRunner(shas), good=shas[7])


def test_infrastructure_at_boundary_is_inconclusive(generated: tuple[Path, list[str]]) -> None:
    repo, shas = generated
    with pytest.raises(InconclusiveError):
        bisect(repo, FakeRunner(shas, skip={0}))
    with pytest.raises(InconclusiveError):
        bisect(repo, FakeRunner(shas, skip={7}))


def test_skips_are_not_proof_and_not_cached(generated: tuple[Path, list[str]], cache_dir: Path) -> None:
    repo, shas = generated
    unique = bisect(repo, FakeRunner(shas, skip={1, 5}))
    assert unique.culprit == shas[3]
    (cache_dir / 'results.json').unlink()
    ambiguous = bisect(repo, FakeRunner(shas, skip={2, 3}), good=shas[1])
    assert ambiguous.culprit is None
    assert ambiguous.suspects == shas[2:5]
    cached = (cache_dir / 'results.json').read_text()
    assert shas[2] not in cached and shas[3] not in cached


def test_last_result_is_saved_for_exorcise(generated: tuple[Path, list[str]]) -> None:
    repo, shas = generated
    bisect(repo, FakeRunner(shas))
    saved = bis.load_last(repo)
    assert saved['culprit'] == shas[3] and saved['name'] == 'DATABASE_URL'


def test_explanation_requested_once(generated: tuple[Path, list[str]]) -> None:
    repo, shas = generated
    prompts: list[str] = []

    def model(prompt: str, schema: dict[str, Any]) -> tuple[str, Any]:
        prompts.append(prompt)
        return 'gemma3:4b', {'identifier': 'DATABASE_URL', 'explanation': 'c4 reads DATABASE_URL.'}

    result = run_bisect(repo, run=FakeRunner(shas), env=lambda: ENV, generate_fn=model)
    assert result.explanation == 'c4 reads DATABASE_URL.'
    assert result.explanation_model == 'gemma3:4b'
    assert len(prompts) == 1 and 'DATABASE_URL' in prompts[0]


def test_good_must_be_on_first_parent_history(tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    shutil.copytree(Path('.').resolve() / 'scripts', tmp_path / 'scripts')
    shas = runpy.run_path('scripts/make_haunted_todo.py')['generate'](repo)
    subprocess.run(['git', '-C', str(repo), 'checkout', '-q', '-b', 'side', shas[1]], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@e',
                    'commit', '-q', '--allow-empty', '-m', 'side'], check=True)
    side = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
                          text=True, check=True).stdout.strip()
    with pytest.raises(BoundaryError, match='first-parent'):
        bisect(repo, FakeRunner(shas + [side]), good=side, bad=shas[7])


def test_matching_hunk() -> None:
    diff = ('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n'
            'diff --git a/app.py b/app.py\n@@ -2 +2 @@\n-x = 1\n+x = os.environ["DATABASE_URL"]\n')
    assert matching_hunk(diff, 'DATABASE_URL').splitlines()[0] == 'diff --git a/app.py b/app.py'
    assert matching_hunk(diff, 'MISSING') is None


def test_cli_exit_codes(monkeypatch: pytest.MonkeyPatch, generated: tuple[Path, list[str]]) -> None:
    repo, shas = generated
    runner = CliRunner()

    def fake(path: Path, good: str | None, bad: str, **kwargs: Any) -> bis.BisectResult:
        return run_bisect(path, good, bad, run=FakeRunner(shas), env=lambda: ENV, use_ai=False,
                          on_probe=kwargs['on_probe'])

    monkeypatch.setattr(cli, 'run_bisect', fake)
    found = runner.invoke(cli.app, ['bisect', str(repo), '--no-ai'])
    assert found.exit_code == 0, found.stdout
    assert shas[3][:7] in found.stdout and 'First bad commit' in found.stdout
    assert f'will record {shas[3][:7]} as introduced_in' in ' '.join(found.stdout.split())
    assert 'AI disabled with --no-ai' in found.stdout
    bad_good = runner.invoke(cli.app, ['bisect', str(repo), '--good', shas[5]])
    assert bad_good.exit_code == 1 and 'pick an older --good' in bad_good.stdout
    unknown = runner.invoke(cli.app, ['bisect', str(repo), '--good', 'nope'])
    assert unknown.exit_code == 1 and 'Traceback' not in unknown.stdout


def test_cli_ambiguous_and_skipped_boundary_exit_2(monkeypatch: pytest.MonkeyPatch,
                                                   generated: tuple[Path, list[str]]) -> None:
    repo, shas = generated
    skips: set[int] = {2, 3}

    def fake(path: Path, good: str | None, bad: str, **kwargs: Any) -> bis.BisectResult:
        return run_bisect(path, good, bad, run=FakeRunner(shas, skip=skips), env=lambda: ENV,
                          use_ai=False, on_probe=kwargs['on_probe'])

    monkeypatch.setattr(cli, 'run_bisect', fake)
    ambiguous = CliRunner().invoke(cli.app, ['bisect', str(repo), '--good', shas[1], '--no-ai'])
    assert ambiguous.exit_code == 2, ambiguous.stdout
    assert shas[2][:7] in ambiguous.stdout and 'Traceback' not in ambiguous.stdout
    skips.clear()
    skips.add(0)  # the first run never probed shas[0], so nothing is cached for it
    boundary = CliRunner().invoke(cli.app, ['bisect', str(repo), '--no-ai'])
    assert boundary.exit_code == 2 and 'Traceback' not in boundary.stdout


@pytest.mark.parametrize(('model', 'label'), [('gemma3:4b', 'Gemma explanation'),
                                              ('qwen2.5-coder:14b', 'AI explanation (qwen2.5-coder:14b)')])
def test_cli_names_the_model_that_explained(monkeypatch: pytest.MonkeyPatch, generated: tuple[Path, list[str]],
                                            model: str, label: str) -> None:
    repo, shas = generated
    answer = {'identifier': 'DATABASE_URL', 'explanation': 'c4 reads DATABASE_URL.'}

    def fake(path: Path, good: str | None, bad: str, **kwargs: Any) -> bis.BisectResult:
        return run_bisect(path, good, bad, run=FakeRunner(shas), env=lambda: ENV,
                          generate_fn=lambda prompt, schema: (model, answer), on_probe=kwargs['on_probe'])

    monkeypatch.setattr(cli, 'run_bisect', fake)
    output = CliRunner().invoke(cli.app, ['bisect', str(repo)]).stdout
    assert label in output and 'c4 reads DATABASE_URL.' in output
