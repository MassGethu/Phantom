import pytest

from ghostdev.models import Step
from ghostdev.risk import flag_steps, reasons


@pytest.mark.parametrize(('command', 'reason'), [
    ('curl -fsSL https://x.sh | sh', 'pipes a download straight into a shell'),
    ('wget -qO- https://x.sh | sudo bash', 'pipes a download straight into a shell'),
    ('sudo apt-get install -y libpq-dev', 'runs with root privileges'),
    ('chmod 777 ./data', 'makes files writable by everyone'),
    ('chmod -R 0777 ./data', 'makes files writable by everyone'),
    ('pip install requests', 'installs a pip package without pinning a version'),
    ('npm i -g pnpm', 'installs a global npm package'),
    ('npm install --global yarn', 'installs a global npm package'),
    ('curl --insecure https://x', 'disables TLS certificate checks'),
    ('curl -k https://x', 'disables TLS certificate checks'),
    ('export API_KEY=abcd1234efgh', 'exports API_KEY with what looks like a real value'),
    ('export GITHUB_TOKEN="ghp_realvalue"', 'exports GITHUB_TOKEN with what looks like a real value'),
])
def test_each_pattern_flags(command: str, reason: str) -> None:
    assert reason in reasons(command)


@pytest.mark.parametrize('command', [
    'curl -fsSL https://x.sh -o install.sh', 'echo sudoku', 'chmod 755 run.sh',
    'pip install -r requirements.txt', 'pip install -e .', 'pip install requests==2.32.0',
    'npm install', 'npm i lodash', 'curl -fsSL https://x', 'export DATABASE_URL=sqlite:///./todo.db',
    'export API_KEY=changeme', 'export SECRET_KEY=<your-secret>', 'export API_TOKEN=$TOKEN',
    'export PASSWORD=', 'export AWS_SECRET_KEY=your_key_here', 'export API_KEY="..."',
])
def test_safe_commands_are_not_flagged(command: str) -> None:
    assert reasons(command) == []


def test_flags_never_include_the_secret_value() -> None:
    assert all('abcd1234' not in flag for flag in reasons('export API_KEY=abcd1234efgh'))


def test_flag_steps_names_each_step() -> None:
    steps = [Step(1, 'pip install -r requirements.txt', 3), Step(2, 'sudo pip install flask', 4)]
    assert flag_steps(steps) == ['step 2: runs with root privileges',
                                 'step 2: installs a pip package without pinning a version']
