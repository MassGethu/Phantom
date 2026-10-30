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

`bisect`, `exorcise`, and `doctor` currently print `not implemented yet`.
No AI calls are made in this phase, with or without `--no-ai`.

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
