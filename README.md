# GhostDev

## Problem

The most dangerous dependency in a repository isn't in requirements.txt.
It's the one that exists only on the maintainer's laptop. GhostDev discovers
undocumented setup assumptions that stop a new contributor's first run.

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

TBD as the first seven implementation commits land.

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
