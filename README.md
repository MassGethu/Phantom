# Phantom

## Problem

The most dangerous dependency in a repository isn't in requirements.txt.
It's the one that exists only on the maintainer's laptop. Phantom discovers
undocumented setup assumptions that stop a new contributor's first run.

Why this matters (2025 research):

- Atlassian found 50% of developers lose 10+ hours a week to inefficiencies;
  finding information and switching contexts are among the largest sources of
  friction. [State of DevEx](https://www.atlassian.com/blog/bitbucket/developer-experience-report-2025).
- Stack Overflow found 66% are frustrated by AI answers that are almost correct,
  and more respondents distrust AI accuracy than trust it.
  [Developer Survey](https://survey.stackoverflow.co/2025/ai).
- DORA found about 90% of technology professionals use AI at work, while stressing
  engineering rigor and fast feedback for sustainable gains.
  [DORA's discussion of its 2025 findings](https://dora.dev/insights/balancing-ai-tensions/).

These figures describe broader development friction, not time spent setting up a
repository. We do not claim a measured setup-time saving.

## How it works

**RUN → NAME → TRACE → FIX → LOCK**

| Stage | Command | What happens |
|---|---|---|
| RUN | `phantom haunt` | Runs the committed README's shell steps in a fresh Docker container |
| NAME | `phantom haunt` | Deterministic rules name the missing requirement; a local model may explain it |
| TRACE | `phantom bisect` | Binary search over first-parent history finds the commit that broke setup |
| FIX | `phantom exorcise` | Patches the README, then proves the fix: it must fail before and pass after on the same commit |
| LOCK | `phantom exorcise`, `phantom doctor` | Records verified requirements in `assumptions.lock` and checks a machine against them |

**AI explains; rules decide.** The model never runs commands, never writes shell, and
can never mark anything verified. Only the before/after container run assigns
`verified_fix` or `verified_together`, and only verified requirements enter the lock.

## Install

Requires Python 3.11+, Git, and Docker Desktop running Linux containers.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
phantom --help
```

AI explanations are optional. Install [Ollama](https://ollama.com) and pull the model:

```bash
ollama pull gemma3:4b
```

## Reproduce the demo

These steps were run on macOS (Apple Silicon, `linux/arm64` containers). The
generator builds `demo/haunted-todo`, an eight-commit repository whose fourth commit
starts reading `DATABASE_URL` without documenting it. The SHAs are identical on
every run.

```bash
python scripts/make_haunted_todo.py
cd demo/haunted-todo
```

**1. Haunt: run setup in a blank container** (exit 1, because a requirement is missing)

```bash
phantom haunt --no-ai
```

```text
Phantom
❌ step 1  python app.py init
╭──────────────────────────────── Setup finding ─────────────────────────────────╮
│ Status                            hypothesis                                   │
│ Type / Name                       env_var / DATABASE_URL                       │
│ Evidence                          KeyError: 'DATABASE_URL'                     │
│ Cause                             rule_matched                                 │
│ Explanation                       AI disabled with --no-ai                     │
│ Suggested fix (not yet verified)  export DATABASE_URL=sqlite:///./todo.db      │
╰────────────────────────────────────────────────────────────────────────────────╯
```

**2. Bisect: find the commit that broke setup** (exit 0 when a culprit is found)

```bash
phantom bisect --no-ai
```

```text
✅ 2e797c7  pass (cached)
❌ 54490e6  fail (cached)
❌ a124636  fail (cached)
✅ 7ae2de7  pass (cached)
✅ 1d15a65  pass (cached)
╭─────────────────────────────── First bad commit ───────────────────────────────╮
│ Commit       a124636                                                           │
│ Author       Haunted Maintainer <maintainer@example.com>                       │
│ Date         2026-10-04T10:00:00+05:30                                         │
│ Subject      refactor: make database location configurable                     │
│ Requirement  env_var / DATABASE_URL                                            │
│ Evidence     KeyError: 'DATABASE_URL'                                          │
│ Explanation  AI disabled with --no-ai                                          │
│                                                                                │
│ .env.example | 1 +                                                             │
│  app.py       | 2 +-                                                           │
│  2 files changed, 2 insertions(+), 1 deletion(-)                               │
│ diff --git a/app.py b/app.py                                                   │
│ @@ -12,7 +12,7 @@ def main() -> None:                                          │
│      add.add_argument("text")                                                  │
│      commands.add_parser("list")                                               │
│      args = parser.parse_args()                                                │
│ -    db_path = "todo.db"                                                       │
│ +    db_path = os.environ["DATABASE_URL"].removeprefix("sqlite:///")           │
│      with sqlite3.connect(db_path) as db:                                      │
│          if args.command == "init":                                            │
│              db.execute("CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY  │
╰────────────────────────────────────────────────────────────────────────────────╯
```

This is the cached rerun (0.3 s); the first run probed the same five commits in about 3 s here.

**3. Exorcise: verify a README fix and write the lock**

```bash
phantom exorcise --no-ai
```

```text
Phantom exorcise  54490e6
❌ before: step 1 `python app.py init` fails
✅ after: every README step passes with the patch (same commit, fresh container)
 Requirement           Evidence                   Cause         Fix verified  Introduced in
 env_var DATABASE_URL  KeyError: 'DATABASE_URL'   rule_matched  ✅ alone      a124636
+# required: DATABASE_URL (added by phantom)
+export DATABASE_URL=sqlite:///./todo.db
Apply these changes? [y/N]:
```

Answering `N` writes nothing. Answering `y` (or passing `--apply`) writes
`README.md` and `assumptions.lock` to the working tree. Phantom never commits:

```yaml
requirements:
- id: env.DATABASE_URL
  type: env_var
  name: DATABASE_URL
  evidence: 'step 1 `python app.py init`: KeyError: ''DATABASE_URL'''
  cause: rule_matched
  introduced_in: a124636
  fix: export DATABASE_URL=sqlite:///./todo.db
  verification: {command_hash: 079f969a…, before: fail, after: pass}
  status: verified_fix
  check: {kind: env_set}
```

**4. Doctor: check your own machine against the lock** (no Docker, no AI)

```bash
phantom doctor
```

```text
Phantom doctor
❌ env.DATABASE_URL  DATABASE_URL is not set
   hint: export DATABASE_URL=sqlite:///./todo.db
1 of 1 checks failed.
```

```bash
DATABASE_URL=sqlite:///./todo.db phantom doctor
```

That run exits 0 with `✅ env.DATABASE_URL`. To reset the demo:

```bash
git checkout README.md && rm assumptions.lock
```

Drop `--no-ai` from haunt or bisect to add a local model's explanation. With
`gemma3:4b` pulled, haunt on the demo shows a validated "Gemma explanation" and took
about 2.5 s here, or about 5 s while the model loads on the first call.

## Commands

| Command | Exit 0 | Exit 1 | Exit 2 |
|---|---|---|---|
| `phantom haunt [PATH] [--rev HEAD] [--no-ai] [--json]` | setup passed | requirement found | infrastructure or inconclusive |
| `phantom bisect [PATH] [--good SHA] [--bad HEAD] [--no-ai]` | culprit found | bad boundary or unreadable history | skips hide the culprit, or infrastructure |
| `phantom exorcise [PATH] [--rev HEAD] [--apply] [--no-ai] [--copilot]` | applied, declined, or nothing to fix | no fix could be verified | dirty README, invalid lock, or inconclusive |
| `phantom doctor [--lock assumptions.lock]` | all checks pass | a check failed | lock missing or invalid |

Errors are one-line messages, never tracebacks. Examples: Docker not running, a
bad `--rev`, a missing README, an unreadable lock, Ollama down, or malformed model
output. These failure modes are covered by tests and were run live.

### haunt

Reads the README and source **as committed** at `--rev`, so uncommitted changes are
excluded. It runs fenced `bash`/`sh`/`shell`/`console`/`zsh` (or untagged) blocks in
one bash session in a fresh `python:3.12-slim` container. A block whose first command
starts with a `$ ` or `% ` prompt is read as a transcript, so only prompted lines
run and example output is ignored. Clone boilerplate is skipped because the
repository is already copied to `/repo`.

The container is never given host mounts or host environment variables. It is
removed afterwards, even after a timeout. The resolved image digest and platform
are recorded.

`--json` prints a single JSON object (`run`, `finding`, `ai_note`, `risk_flags`)
with no spinner or prose.

Haunt also prints **risk flags** for risky README commands. They are warnings only:
they never block a run or change the exit code. The flagged patterns are:
- `curl`/`wget` piped into a shell
- `sudo`
- `chmod 777`
- unpinned `pip install`
- `npm i -g`
- `--insecure` or `curl -k`
- exporting a `*TOKEN`, `*SECRET`, `*KEY` or `*PASSWORD` variable with a
  non-placeholder value. The flag names the variable but never prints the value.

### AI explanations

Missing-command diagnosis also works with `--no-ai`: rules recognize shell errors
and generic messages such as `Could not find executable 'cmake'` or
`no 'java' command could be found`. Explicit missing-environment-variable messages
are recognized too. If an error names both a missing executable and a related
variable (such as `JAVA_HOME`), the missing executable takes priority. These are
hypotheses based on captured output; missing tools do not get automatic install fixes.

Without `--no-ai`, Phantom asks Ollama for schema-constrained JSON at temperature 0,
with a 20 s timeout. It uses `gemma3:4b` and falls back to `qwen2.5-coder:14b` if
Gemma is not pulled. The output label names whichever model actually answered.

An explanation is kept only if all of these hold:
- its evidence line appears verbatim in the step output;
- it names the same requirement as the rule;
- any version claim matches the repository's version files.

Anything else is dropped, and the output says why. If Ollama is unreachable, the
rule finding is still shown.

### bisect

Parses HEAD's README once and runs those same commands against older commits, using
a first-parent binary search. `--good` must pass, `--bad` must fail, and `--good`
must be on `--bad`'s first-parent history.

Infrastructure failures count as *skips*, never as broken code. If skips hide the
culprit, the possible range is printed. Pass and fail results are cached in
`~/.cache/phantom/` by commit, image digest, platform and command hash; skips are
never cached. The model is asked once, after the search, to explain the culprit's
diff.

### exorcise

Refuses to run if the working-tree README differs from the committed one. It runs
the original README (which must fail). It then inserts a fixed-template command
directly before the failing line: `export NAME='value'` with shell quoting, or
`cp NAME.example NAME`. Finally it re-parses the patched README and runs it on the
same commit in a new container (which must pass).

Up to three rounds handle a later step failing next. Each fix is also re-tested
alone; fixes that only pass together are marked `verified_together`. Requirements
no rerun proved are listed as "needs a human" and never enter the lock. Existing
lock entries are merged by `id`. `introduced_in` is filled from the last
`phantom bisect` of the same requirement, and only when that bisect used the same
README steps and its commits are ancestors of the revision being verified.

`--copilot` also proposes `.github/workflows/copilot-setup-steps.yml` for the GitHub
Copilot coding agent. The workflow is built only from the patched README commands
that just passed in the container, and they run in one bash step to match the
single shell session they were verified in: no `errexit` or `pipefail`, and the job
stops at the first step that exits non-zero, as the verifier does. It appears in the
same diff and the same confirmation, and an existing workflow is never overwritten.

### doctor

Checks each lock entry locally:
- `env_set`: the variable is set. The value is never printed or validated.
- `file_exists`: the file exists.
- `which`: the tool is on PATH.
- `version`: compares `tool --version` against the spec.
- `tcp`: something accepts a connection on that localhost port.

Each failure prints the entry's `fix` as a hint.

## Configuration

| Variable | Default |
|---|---|
| `PHANTOM_IMAGE` | `python:3.12-slim` |
| `PHANTOM_MODEL` | `gemma3:4b` (fallback `qwen2.5-coder:14b`) |
| `OLLAMA_HOST` | `http://localhost:11434` |

## Development

```bash
pytest -q
pytest -q -m docker
```

The default run uses fake runners and fake models and needs neither Docker nor
Ollama. Docker tests skip when the daemon is unavailable. With Docker they cover:
- the broken HEAD and the passing third commit;
- persistent shell exports across steps;
- that the host environment is not forwarded;
- timeout cleanup and no leftover containers;
- live bisect and before/after verification.

`python scripts/make_haunted_todo.py --force` regenerates the demo with identical SHAs.

## Limitations

- Only fenced shell blocks in the root README are treated as setup. Other docs,
  Makefiles and scripts are not read.
- Only environment variables and `.example` files get executable fixes. Missing
  tools, services and versions are reported but never auto-fixed.
- `doctor` checks that an env var is set, not that its value works. `tcp` checks
  that a port is open, not which service is listening.
- `cp NAME.example NAME` fixes look for the example file at the repository root,
  even when the failing step ran in a subdirectory.
- README commands run with network access inside containers. Containers are not a
  security boundary for hostile repositories.
- The Copilot workflow is verified in the `python:3.12-slim` container, not on a
  GitHub runner, and is only emitted when exorcise verified a fix (not when the README
  already passes).
- The lock records the image by digest (`python@sha256:…`) without its tag.
- Tested only on macOS with Apple Silicon and the generated demo. No arbitrary
  repository or Windows host support is promised.
- No terminal recording is included; the transcripts above are captured output,
  captured at 82 columns (the exorcise table and lock excerpt are condensed).

## Roadmap

Not built yet:
- `phantom ward`: a CI check that blocks PRs adding new undocumented assumptions.
- Devcontainer and AGENTS.md output from verified steps.
- Contributor personas, such as Windows or Mac setups.
- Verified fixes for missing tools, services and versions.
- Support for setup instructions outside the README.

## AI tools used

- **OpenAI Codex**: phase one implementation and tests (scaffold, models, parser,
  Docker runner, rules, haunt CLI).
- **Claude Code**: phase two implementation and tests (AI diagnosis, bisect, verify,
  exorcise, lock file, doctor, risk flags, hardening, docs).
- **Gemma (`gemma3:4b`) via Ollama**: optional local explanations at runtime. It is
  never trusted to execute commands or decide verification. Qwen
  (`qwen2.5-coder:14b`) is the fallback model.

## Credits

Runtime libraries: [Typer](https://typer.tiangolo.com) (CLI),
[Rich](https://github.com/Textualize/rich) (terminal rendering) and
[PyYAML](https://pyyaml.org) (lock files). Ollama is called with the standard
library's `urllib`. Development uses pytest; packaging uses setuptools. Git, Docker
and the official `python:3.12-slim` image provide the execution infrastructure.

## License

MIT; see [LICENSE](LICENSE). Install from source; this package is not published.
