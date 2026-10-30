# GhostDev

## Problem

The most dangerous dependency in a repository isn't in requirements.txt.
It's the one that exists only on the maintainer's laptop. GhostDev discovers
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

**RUN → NAME → TRACE → FIX → LOCK**: run README setup in a fresh container,
name missing requirements with deterministic rules, then trace their origin,
verify documentation fixes, and record requirements. This first phase implements
RUN and rule-based NAME; remaining commands are placeholders.

## Install

Requires Python 3.11+ and Docker Desktop running Linux containers.

```sh
pip install -e '.[dev]'
```

Optional AI for the next phase: install Ollama and run `ollama pull gemma3:4b`.
Configuration: `GHOST_IMAGE`, `GHOST_MODEL`, and `OLLAMA_HOST`.

## Usage

```sh
python scripts/make_haunted_todo.py
ghost haunt demo/haunted-todo --no-ai
ghost haunt demo/haunted-todo --no-ai --json
```

The demo's first setup step fails with `KeyError: 'DATABASE_URL'`. GhostDev reports
`env_var`, `rule_matched`, and suggests
`export DATABASE_URL=sqlite:///./todo.db`, labelled **not yet verified**. Finding a
failure exits 1; a passing setup exits 0; infrastructure or inconclusive results
exit 2. JSON output contains `run` metadata and the `finding` (or null).

`ghost haunt [PATH] --rev HEAD` reads committed README commands and source at that
revision. Uncommitted changes are excluded. Clone boilerplate is skipped because
the repository is already copied to `/repo`. The runner records the resolved
image digest and platform (Linux ARM64 on Apple Silicon).

Without `--no-ai`, haunt asks a local model through Ollama (`gemma3:4b`, falling
back to `qwen2.5-coder:14b` when Gemma is not pulled) to explain the finding. The
answer is schema-constrained JSON and is kept only when its evidence line appears in
the step output and it names the same requirement as the rule. When Ollama is
unreachable, haunt still shows the rule finding and says why no explanation appears.

Haunt also flags risky README commands as warnings; they never block a run or change
the exit code. The flagged patterns are piping `curl`/`wget` into a shell, `sudo`,
`chmod 777`, unpinned `pip install`, `npm i -g`, `--insecure`/`curl -k`, and exporting
a `*TOKEN`, `*SECRET`, `*KEY` or `*PASSWORD` variable with a non-placeholder value.
The flag names the variable but never prints the value.

`ghost bisect [PATH] [--good SHA] [--bad HEAD] [--no-ai]` parses HEAD's README
once and runs those same commands against older commits in fresh containers, using
a first-parent binary search. `--good` must pass and `--bad` must fail, or bisect
exits 1 with a message. Infrastructure failures are skipped, never counted as
broken code; if skips hide the culprit, the possible range is reported (exit 2).
Results are cached in `~/.cache/ghostdev/results.json` by commit, image digest,
platform and command hash, so a rerun is instant. The model is asked once, after
the search, to explain the culprit's diff. On the demo the culprit is commit four.

`ghost exorcise [PATH] [--rev HEAD] [--apply] [--no-ai]` refuses to run when the
working-tree README differs from the committed one. It runs the original README
(must fail), inserts a fixed-template command such as
`export DATABASE_URL=sqlite:///./todo.db` directly before the failing line, re-parses
the patched README and runs it on the same commit in a new container (must pass).
Up to three rounds handle a later step failing next; fixes that only pass together
are marked `verified_together`. It shows the diff and asks `Apply these changes?
[y/N]` unless `--apply` is given, then writes `README.md` and `assumptions.lock`.
It never commits. Requirements no rerun proved are listed as "needs a human" and
never enter the lock.

`ghost doctor [--lock assumptions.lock]` checks this machine against the lock without Docker or AI: env vars are set (values are never printed), files exist, tools are on PATH, versions match, and localhost ports accept connections. Exit 0 when all pass, 1 when any fail, 2 when the lock is missing or invalid.

### Development and handoff

```sh
pytest -q
pytest -q -m docker
```

Docker tests skip when the daemon is unavailable. With Docker running they cover
broken HEAD, passing commit three, persistent shell exports, timeout handling,
and container cleanup. The generated demo has its own eight-commit history;
`python scripts/make_haunted_todo.py --force` regenerates identical commit SHAs.

The next implementation step is Gemma diagnosis. Shared models are in
`models.py`, pure AI validators in `rules.py`, and the injectable runner interface
is `runner.RunFn`. Only future verification code may set `VERIFIED_*`.

## Limitations

Only root README fenced shell setup is supported. README commands execute in
containers with network access, no host mounts, and no host environment passed
through. Containers are not a security boundary for hostile repositories.
Rules propose causes; suggested fixes are unverified until the later verification
phase. No arbitrary-repository or Windows-host support is promised.

## Roadmap

Gemma explanations with evidence validation, first-bad-commit search, verified
README fixes, assumptions.lock, local doctor checks, and risk warnings.
Later: CI checks, contributor personas and devcontainer output.

## AI tools used

- OpenAI Codex: initial implementation and tests.
- Claude Code: planned teammate refinement and phase two implementation.
- Gemma via Ollama: planned local explanations; never trusted to execute commands.

## Credits

Runtime libraries: Typer (CLI), Rich (terminal rendering), PyYAML (lock files).
Development: pytest; packaging: setuptools. Python standard library, Git,
Docker and the official python:3.12-slim image provide execution infrastructure.
Ollama hosts the optional Gemma model; Qwen is the planned fallback model.

## License

MIT; see [LICENSE](LICENSE). Install from source; do not publish to PyPI or npm.
