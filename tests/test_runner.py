from ghostdev.models import Step
from ghostdev.runner import build_script, parse_output


def test_marker_parsing() -> None:
    steps = [Step(1, 'echo hi', 1), Step(2, 'false', 2), Step(3, 'true', 3)]
    output = '::ghost-step 1::\nhi\n::ghost-rc 1 0::\n::ghost-step 2::\nerror\n::ghost-rc 2 1::\n'
    results = parse_output(output, steps)
    assert [(r.step.index, r.exit_code, r.output) for r in results] == [(1, 0, 'hi\n'), (2, 1, 'error\n')]


def test_timeout_and_shell_exit() -> None:
    steps = [Step(1, 'sleep 99', 1)]
    results = parse_output('::ghost-step 1::\npartial', steps, timed_out=True)
    assert results[0].timed_out and results[0].exit_code is None
    assert results[0].output == 'partial'
    assert parse_output('::ghost-step 1::\n', steps, process_code=3)[0].exit_code == 3
    assert build_script(steps).endswith('exit 0\n')
