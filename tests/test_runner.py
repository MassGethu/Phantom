from pathlib import Path
import subprocess

import pytest

from phantom import config, render
from phantom.models import RunResult, Step, StepResult
from phantom.runner import (_durations, _stream, build_script, command_lines, error_message,
                            is_long_running, parse_output)


def test_marker_parsing() -> None:
    steps = [Step(1, 'echo hi', 1), Step(2, 'false', 2), Step(3, 'true', 3)]
    output = '::phantom-step 1::\nhi\n::phantom-rc 1 0::\n::phantom-step 2::\nerror\n::phantom-rc 2 1::\n'
    results = parse_output(output, steps)
    assert [(r.step.index, r.exit_code, r.output) for r in results] == [(1, 0, 'hi\n'), (2, 1, 'error\n')]


def test_timeout_and_shell_exit() -> None:
    steps = [Step(1, 'sleep 99', 1)]
    results = parse_output('::phantom-step 1::\npartial', steps, timed_out=True)
    assert results[0].timed_out and results[0].exit_code is None
    assert results[0].output == 'partial'
    assert parse_output('::phantom-step 1::\n', steps, process_code=3)[0].exit_code == 3
    assert build_script(steps).endswith('exit 0\n')


def test_unreachable_docker_gets_a_friendly_message() -> None:
    error = subprocess.CalledProcessError(1, ['docker'], stderr=(
        b'failed to connect to the docker API at unix:///x.sock; check if the path is correct '
        b'and if the daemon is running: dial unix /x.sock: connect: no such file or directory'))
    assert error_message(error) == "Docker isn't running. Start Docker Desktop and retry."


@pytest.mark.parametrize('command', [
    'npm start', 'npm run dev', 'yarn dev', 'pnpm run start:prod', 'PORT=3000 npm start',
    'flask run', 'flask --app hello run', 'python -m flask run --debug', 'python manage.py runserver',
    'poetry run python manage.py runserver', 'uv run uvicorn app:app', 'uvicorn main:app --reload',
    'python -m http.server 8000', 'fastapi dev main.py', 'bundle exec rails s', 'rails server',
    'bundle exec jekyll serve', 'hugo server -D', 'streamlit run app.py', 'npx vite', 'next dev',
    'php artisan serve'])
def test_servers_are_long_running(command: str) -> None:
    assert is_long_running(command)


@pytest.mark.parametrize('command', [
    'npm install', 'npm run build', 'npm test', 'npm run devtools-check', 'yarn', 'vite build',
    'flask db upgrade', 'python manage.py migrate', 'rails db:migrate', 'pip install uvicorn',
    'python app.py init', 'npm start &'])
def test_ordinary_commands_are_not(command: str) -> None:
    assert not is_long_running(command)


def run_bash(script: str) -> subprocess.CompletedProcess[str]:
    # Fed on stdin exactly like `docker exec -i ... bash -s`.
    return subprocess.run(['bash', '-s'], input=script, capture_output=True, text=True, timeout=30)


def test_server_that_stays_up_is_started_and_cannot_eat_the_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, 'SERVER_GRACE', 1)
    # A fake server that would swallow the rest of the script if it could read stdin.
    lines = ['nodemon() { echo listening; cat > /dev/null; sleep 3; }', *command_lines(2, 'nodemon'),
             'echo "rc=$__rc"', 'echo after']
    result = run_bash('\n'.join(lines) + '\n')
    assert '::phantom-started 2::' in result.stdout and 'listening' in result.stdout
    assert 'rc=0' in result.stdout and 'after' in result.stdout


def test_server_that_exits_early_keeps_its_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, 'SERVER_GRACE', 1)
    lines = [*command_lines(1, 'PATH=/nonexistent flask run'), 'echo "rc=$__rc"']
    result = run_bash('\n'.join(lines) + '\n')
    assert 'rc=127' in result.stdout and 'command not found' in result.stdout
    assert 'phantom-started' not in result.stdout


def test_started_marker_and_note(monkeypatch: pytest.MonkeyPatch) -> None:
    steps = [Step(1, 'npm start', 1)]
    results = parse_output('::phantom-step 1::\nlistening\n\n::phantom-started 1::\n::phantom-rc 1 0::\n', steps)
    assert results[0].started and results[0].exit_code == 0 and results[0].output == 'listening\n\n'
    assert '</dev/null' in build_script(steps)
    monkeypatch.setattr(render.console, 'width', 160)
    with render.console.capture() as captured:
        render.haunt_result(RunResult('a' * 40, 'img', 'p', [StepResult(steps[0], 0, '', started=True)], True), None)
    assert 'npm start  (started, still running after 10s)' in captured.get()


def test_stream_reports_each_step_as_it_starts_and_times_it() -> None:
    steps = [Step(1, 'sleep 0.3', 1), Step(2, 'true', 2)]
    script = ''.join(f"printf '::phantom-step {s.index}::\\n'; {s.command}; printf '::phantom-rc {s.index} 0::\\n'\n"
                     for s in steps)
    seen: list[tuple[int, int]] = []
    execution = _stream(['bash', '-s'], script, steps, 10, lambda step, total: seen.append((step.index, total)))
    assert seen == [(1, 2), (2, 2)] and execution.returncode == 0 and not execution.timed_out
    results = parse_output(execution.output, steps, process_code=execution.returncode)
    _durations(results, execution.times)
    assert results[0].duration >= 0.3 and results[1].duration < 0.3


def test_stream_timeout_kills_the_process_and_keeps_partial_output() -> None:
    steps = [Step(1, 'sleep 30', 1)]
    execution = _stream(['bash', '-s'], "printf '::phantom-step 1::\\npartial\\n'; sleep 30\n", steps, 1, None)
    assert execution.timed_out and 'partial' in execution.output
    results = parse_output(execution.output, steps, timed_out=True)
    _durations(results, execution.times)
    assert results[0].timed_out and results[0].duration >= 0.9


def test_step_line_shows_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render.console, 'width', 160)
    step = StepResult(Step(1, 'npm install', 1), 0, '', duration=4.24)
    with render.console.capture() as captured:
        render.haunt_result(RunResult('b' * 40, 'img', 'linux/arm64', [step], True), None)
    output = captured.get()
    assert 'step 1  npm install  4.2s' in output
    assert f'Phantom haunt · {Path.cwd().name} · @ bbbbbbb · {config.IMAGE} · linux/arm64' in output
