"""Shared defaults; configuration never forwards host environment to Docker."""

import os
from pathlib import Path

APP_NAME = "GhostDev"
IMAGE = os.environ.get("GHOST_IMAGE", "python:3.12-slim")
MODEL = os.environ.get("GHOST_MODEL", "gemma3:4b")
FALLBACK_MODEL = "qwen2.5-coder:14b"
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
RUN_TIMEOUT = 120
DOCKER_TIMEOUT = 180
GIT_TIMEOUT = 30
AI_TIMEOUT = 20
MAX_ROUNDS = 3
OUTPUT_TAIL_LINES = 60
MAX_EXPLANATION_CHARS = 400
MAX_DIFF_CHARS = 6000
CACHE_DIR = Path.home() / ".cache" / "ghostdev"
