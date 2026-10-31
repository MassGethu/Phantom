from pathlib import Path

from phantom.parse import extract_steps, extract_steps_from_text, find_readme


def test_languages_prompts_and_console_output() -> None:
    text = '''```bash
$ echo bash
```
```python
print("ignored")
```
```
% echo untagged
```
```console
$ echo hello
hello
% echo bye
bye
```
```zsh
echo zsh
```
```sh
echo sh
```
```shell
echo shell
```
'''
    assert [s.command for s in extract_steps_from_text(text)] == [
        "echo bash", "echo untagged", "echo hello", "echo bye", "echo zsh", "echo sh", "echo shell"]


def test_continuation_source_and_clone() -> None:
    text = '# Setup\n```bash\n# comment\n\ngit clone https://example.com/a\ncd a\n$ echo one \\\n  two\ncd subdir\n```\n'
    steps = extract_steps_from_text(text)
    assert [(s.index, s.command, s.source_line) for s in steps] == [
        (1, "echo one two", 7), (2, "cd subdir", 9)]


def test_untagged_transcript_skips_output_lines() -> None:
    click = '```\n$ python hello.py --count=3\nYour name: Click\nHello, Click!\n```\n'
    flask = '```\n$ flask run\n  * Running on http://127.0.0.1:5000/ (Press CTRL+C to quit)\n```\n'
    sh = '```sh\n$ pip install -e . \\\n  --quiet\nSuccessfully installed\n% make\n```\n'
    assert [s.command for s in extract_steps_from_text(click + flask + sh)] == [
        "python hello.py --count=3", "flask run", "pip install -e . --quiet", "make"]


def test_transcript_state_resets_per_block() -> None:
    text = '```\n$ echo one\nout\n```\n```bash\necho two\n$ echo three\necho four\n```\n'
    assert [s.command for s in extract_steps_from_text(text)] == [
        "echo one", "echo two", "echo three", "echo four"]


def test_console_continuation_and_tilde_fence() -> None:
    steps = extract_steps_from_text('~~~console\n$ echo one \\\n two\none two\n~~~')
    assert [s.command for s in steps] == ["echo one two"]


def test_case_insensitive_readme(tmp_path: Path) -> None:
    (tmp_path / 'ReadMe.MD').write_text('```\necho ok\n```')
    assert find_readme(tmp_path).name == 'ReadMe.MD'
    assert extract_steps(tmp_path)[0].command == 'echo ok'


def test_demo_readme() -> None:
    # The generator's README constant keeps this unit test independent of demo generation.
    import runpy
    readme = runpy.run_path('scripts/make_haunted_todo.py')['README']
    steps = extract_steps_from_text(readme)
    assert [s.command for s in steps] == [
        'python app.py init', 'python app.py add "buy milk"', 'python app.py list']
    assert [s.source_line for s in steps] == [7, 8, 9]
