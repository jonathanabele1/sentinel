# Python tooling overview

## What it is

A modern Python project usually doesn't run on Python alone. Around it sits a small constellation of tools that handle dependencies, code style, type safety, tests, and task running. Sentinel uses five:

```
┌──────────────────────────────────────────────────────────────┐
│ make    A command shortcut runner. You type "make check"     │
│         and it runs whatever commands the Makefile says.     │
│                                                              │
│ uv      A Python package manager + project runner. It owns   │
│         the virtualenv, installs deps, and runs commands     │
│         inside that env via "uv run <cmd>".                  │
│                                                              │
│ ruff    A Python linter + formatter. Two jobs:               │
│         1. lint  - reads your code, flags problems.          │
│         2. format - rewrites your code so spacing,           │
│            import order, etc. are consistent.                │
│                                                              │
│ mypy    A static type checker. Reads your type hints and     │
│         catches "you said this is a str, but here it's None" │
│         bugs without running the code.                       │
│                                                              │
│ pytest  Runs your tests.                                     │
└──────────────────────────────────────────────────────────────┘
```

None of these are doing anything mysterious. They are all just programs that read your source files and either complain about them or rewrite them.

## How they fit together

A useful way to picture the stack:

```
   You type a command
         │
         ▼
   ┌──────────┐
   │   make   │   reads the Makefile, runs the underlying command
   └────┬─────┘
        │
        ▼
   ┌──────────┐
   │   uv     │   "uv run X" = run X inside the project virtualenv
   └────┬─────┘
        │
        ▼
   ┌──────────────────────────────────────┐
   │ ruff / mypy / pytest / alembic / ... │   the actual tool
   └──────────────────────────────────────┘
```

`make` is optional convenience. `uv run` is the part that activates the right Python and the right dependencies. The actual work is done by whichever tool you invoked.

A common point of confusion: you can call any of these tools directly without `make`, and you can also call them outside `uv run` if you've activated the venv another way. Make and uv are layers of convenience, not requirements.

## What `make check` actually does

`make check` is the single command worth memorising. Open the Makefile and you'll find:

```make
check: lint typecheck test-unit
```

That line says "to do `check`, first do `lint`, then `typecheck`, then `test-unit`." Each of those is its own target:

```make
lint:
	uv run ruff check .

typecheck:
	uv run mypy

test-unit:
	uv run pytest tests/unit
```

So `make check` is literally three commands in a row:

1. `uv run ruff check .` — ruff scans every Python file and prints problems.
2. `uv run mypy` — mypy reads the type annotations and checks them.
3. `uv run pytest tests/unit` — pytest runs every test in `tests/unit/`.

If any step exits non-zero, `make` halts and reports "Error 1." If all three pass, you're clear to push.

## Why these specific tools

| Tool | What it replaces | Why we picked it |
| --- | --- | --- |
| **make** | A shell script you'd have to write yourself | Universally preinstalled. Documents the project's commands in one place. CI and humans use the same entrypoints. |
| **uv** | pip + pip-tools + virtualenv + pyenv | One tool, written in Rust, dramatically faster than the pip stack. Handles Python version, venv, and lockfile in one shot. |
| **ruff** | flake8 + isort + black + pyupgrade + bandit + several others | One tool that subsumes the entire Python linting/formatting ecosystem. 100x faster because it's written in Rust. |
| **mypy** | "hope my code is right" | Catches type errors at edit time instead of in production. Strict mode is a hard requirement for a portfolio piece. |
| **pytest** | unittest | Better assertions, fixtures, parametrise decorator, ecosystem of plugins. The de facto Python testing tool. |

The Rust theme is not an accident. The 2020s Python tooling revival is largely "rewrite the slow Python tool in Rust"; the result is workflows that take seconds instead of minutes. Ruff lints the entire Sentinel codebase in under 50ms.

## Your daily loop

```bash
# While writing code, occasionally:
make format         # ruff rewrites whatever drifted
make check          # full sanity: lint + types + unit tests

# When something looks weird, target one file:
uv run pytest tests/unit/test_signature.py -v
uv run mypy apps/api/main.py
uv run ruff check apps/api/main.py
```

That is the whole loop. `make` is the shortcut, `uv run` is the "inside the venv" prefix, `ruff` / `mypy` / `pytest` are the tools doing work.

## Why it matters for Sentinel specifically

1. **CI runs these same gates on every push.** If lint, types, or tests fail, GitHub blocks the merge. `make check` runs the same gates locally so you catch problems before pushing.
2. **A reviewer evaluating your portfolio will look at your CI config.** Strict lint + strict mypy + tests on every PR is the table-stakes signal that you ship production code.
3. **Sentinel's whole thesis is "deterministic, auditable, reliable."** That story falls apart if your own codebase is sloppy. A PR review bot needs to live in a repo that itself looks production-grade.

## TL;DR

Five tools: `make` (shortcuts), `uv` (env + deps), `ruff` (lint + format), `mypy` (types), `pytest` (tests). `make check` runs the three quality gates in one shot. `uv run X` is the prefix that puts you inside the project's virtualenv before running X. The Rust-based tools (uv, ruff) are dramatically faster than the older pip+flake8 stack and that's why they've taken over.

## Interview-style questions

1. What does `uv run` add when you put it in front of a command? What goes wrong if you forget it?
2. Why is `make check` divided into three sub-targets instead of one big script?
3. You see a developer run `pip install requests` inside this project. What's the problem with that?
4. Ruff replaces several older tools at once. Name three of them and what each did.
5. Mypy strict mode is enabled in `pyproject.toml`. What does that actually change vs default mypy?
6. Could you delete the Makefile entirely and still work on this project? What would you lose?
