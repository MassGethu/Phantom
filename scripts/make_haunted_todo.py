"""Generate a disposable, reproducible eight-commit demo repository."""

import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1] / "demo" / "haunted-todo"
README = '''# Haunted Todo

## Setup
```bash
git clone https://github.com/example/haunted-todo.git
cd haunted-todo
python app.py init
python app.py add "buy milk"
python app.py list
```
'''
APP = '''"""A tiny SQLite todo list."""
import argparse
import os
import sqlite3


def main() -> None:
    parser = argparse.ArgumentParser(description="A todo list")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    add = commands.add_parser("add")
    add.add_argument("text")
    commands.add_parser("list")
    args = parser.parse_args()
    db_path = "todo.db"
    with sqlite3.connect(db_path) as db:
        if args.command == "init":
            db.execute("CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, text TEXT)")
        elif args.command == "add":
            db.execute("INSERT INTO todos (text) VALUES (?)", (args.text,))
        else:
            for item_id, text in db.execute("SELECT id, text FROM todos ORDER BY id"):
                print(text)


if __name__ == "__main__":
    main()
'''
MESSAGES = [
    "feat: initial todo app", "feat: show item ids in list",
    "docs: describe the project", "refactor: make database location configurable",
    "feat: improve help text", "style: align list output",
    "docs: add code comments", "refactor: extract connect() helper",
]


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


def generate(repo: Path = ROOT, force: bool = False) -> list[str]:
    if repo.exists():
        if not force:
            raise FileExistsError(f"{repo} already exists; use --force to regenerate it")
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    git(repo, "-c", "init.defaultBranch=main", "init")
    git(repo, "config", "core.autocrlf", "false")
    readme, app = README, APP
    shas: list[str] = []
    start = datetime.fromisoformat("2026-10-01T10:00:00+05:30")
    for index, message in enumerate(MESSAGES):
        if index == 1:
            app = app.replace("print(text)", 'print(f"{item_id}: {text}")')
        elif index == 2:
            readme += "\nA small, stdlib-only todo app for contributors.\n"
        elif index == 3:
            app = app.replace('db_path = "todo.db"',
                              'db_path = os.environ["DATABASE_URL"].removeprefix("sqlite:///")')
            (repo / ".env.example").write_text("DATABASE_URL=sqlite:///./todo.db\n")
        elif index == 4:
            app = app.replace('description="A todo list"',
                              'description="Create and list your SQLite todo items"')
        elif index == 5:
            app = app.replace('{item_id}: {text}', '{item_id:>3}: {text}')
        elif index == 6:
            app = app.replace('    with sqlite3.connect',
                              '    # SQLite commits changes when the context exits.\n    with sqlite3.connect')
        elif index == 7:
            app = app.replace('\ndef main() -> None:', '\ndef connect(db_path: str) -> sqlite3.Connection:\n    return sqlite3.connect(db_path)\n\n\ndef main() -> None:')
            app = app.replace('with sqlite3.connect(db_path)', 'with connect(db_path)')
        (repo / "README.md").write_text(readme)
        (repo / "app.py").write_text(app)
        (repo / ".gitignore").write_text("*.db\n__pycache__/\n")
        date = (start + timedelta(days=index)).isoformat()
        env = {**os.environ, "GIT_AUTHOR_NAME": "Haunted Maintainer",
               "GIT_COMMITTER_NAME": "Haunted Maintainer",
               "GIT_AUTHOR_EMAIL": "maintainer@example.com",
               "GIT_COMMITTER_EMAIL": "maintainer@example.com",
               "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        git(repo, "add", ".")
        git(repo, "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "commit", "-m", message, env=env)
        shas.append(git(repo, "rev-parse", "HEAD"))
    return shas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        shas = generate(force=args.force)
    except FileExistsError as error:
        parser.exit(1, f"{error}\n")
    print(f"Created {ROOT} with {len(shas)} commits; culprit: {shas[3]}")


if __name__ == "__main__":
    main()
