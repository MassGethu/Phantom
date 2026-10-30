import subprocess

from phantom.models import Step
from phantom.runner import build_script, error_message, parse_output


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
