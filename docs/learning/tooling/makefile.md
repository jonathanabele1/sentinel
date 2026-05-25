# Make and the Makefile

## What it is

`make` is a Unix utility that reads a file called `Makefile` and executes the recipes inside it. The original 1976 use case was C compilation: "to build this `.o` file, run this `gcc` command on this `.c` file." But the tool is general-purpose enough that modern projects use it as a **task runner**, a way to give long shell commands short memorable names.

A Makefile entry looks like this:

```make
target: dependency1 dependency2
	shell command to run
	another shell command
```

The lines under the target start with **tabs**, not spaces. This is the one syntactic gotcha that bites everyone once. If you copy-paste a Makefile and `make` complains about "missing separator," check that you have tabs.

## The Sentinel Makefile

Open it. The structure is straightforward:

```make
.PHONY: help install dev up down logs migrate test test-unit test-integration lint typecheck format check clean

help:
	@echo "Sentinel — common commands"
	...

install:
	uv sync

up:
	docker compose -f infra/docker-compose.yml up -d
	@echo "Waiting for services..."
	@sleep 3
	@docker compose -f infra/docker-compose.yml ps

down:
	docker compose -f infra/docker-compose.yml down

migrate:
	uv run alembic upgrade head

dev: up migrate
	uv run uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy

format:
	uv run ruff format .
	uv run ruff check --fix .

check: lint typecheck test-unit

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist
	find . -type d -name __pycache__ -exec rm -rf {} +
```

A few patterns worth pointing at:

### `.PHONY`

`make` historically assumed targets were filenames. If a file named `lint` existed in the current directory, `make lint` would say "nothing to do, lint is already up to date." `.PHONY: lint` tells make "this is not a file; always run the recipe regardless of what's on disk." Every target in our Makefile is phony because none of them produce a file we'd want to track.

### The `@` prefix

`@echo "Waiting..."` runs the command but doesn't print the command itself to stdout. Without the `@`, `make` echoes every command before running it (helpful for debugging, noisy for normal use). We use `@` on `echo` and `sleep` because the commands themselves aren't interesting to see.

### Dependencies

`dev: up migrate` means "before running `dev`'s recipe, run `up` and `migrate` first." That's how `make dev` becomes "boot Docker → run migrations → start the API" in one command. `check: lint typecheck test-unit` works the same way: three sub-targets get run in order before `check` itself does anything (in our case, nothing, because `check` has no recipe of its own).

### `make help`

By convention, a Makefile's first target should print usage information so `make` with no argument (or `make help`) tells you what's available. Our `help` recipe echoes the full command list.

## What `make` doesn't do

A common misconception: `make` does not parallelise or sandbox or do anything fancy by default. It just runs shell commands in order. If you want parallelism, you pass `make -j4` (run up to 4 jobs in parallel) and it's still up to you to make the targets independent.

It also doesn't know anything about Python, uv, Docker, or Sentinel. It's just running whatever strings appear in the recipe. The Makefile is documentation of what to run.

## Why we use it

1. **It documents the project's commands in one place.** A new contributor (including future-you returning after six months) types `make help` and sees the vocabulary. No grepping through CI configs or Slack history.
2. **`make` is preinstalled everywhere on Unix.** No tool to install before you can run anything else. On macOS it ships with Xcode Command Line Tools; on Linux it's usually preinstalled.
3. **CI and humans use the same entrypoints.** When `make check` works locally and `make check` runs in CI, "works on my machine" gets a lot more honest.
4. **Short names.** `make dev` beats typing `docker compose -f infra/docker-compose.yml up -d && uv run alembic upgrade head && uv run uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000` every time.

## Alternatives we did not choose

- **`just`** — Modern Makefile replacement, similar syntax, no tab gotcha. Requires installing `just` separately, which is a setup-friction tax.
- **`task`** (Taskfile.yml) — YAML-based, declarative, nice features. Also requires a separate install.
- **Shell scripts** — One `.sh` file per command works fine but spreads the project's command surface across multiple files instead of one.
- **`npm scripts`-style entries in `pyproject.toml`** — Possible via `[project.scripts]` or third-party plugins, but discoverability is worse: there's no single command that lists all available targets.

The Makefile won on universality. If you decided to delete it, the project would still work; you'd just type out the long commands.

## Common targets explained

| Target | What it does | When to use |
| --- | --- | --- |
| `make help` | Lists available targets | When you forget what's available |
| `make install` | Runs `uv sync` (installs deps from the lockfile) | First time on a new machine, or after pulling new deps |
| `make up` | Starts Postgres, Redis, Jaeger | Before doing anything that touches the DB or traces |
| `make down` | Stops those services | End of day, or to reclaim resources |
| `make migrate` | Runs Alembic migrations | After pulling new schema changes |
| `make dev` | up + migrate + uvicorn (the full local startup) | When you want to develop |
| `make test` | Full pytest run with coverage | Before pushing |
| `make test-unit` | Just `tests/unit/` (no infra needed) | Tight inner loop while writing |
| `make lint` | ruff check | Pre-push, or after a refactor |
| `make typecheck` | mypy strict | Pre-push, or after touching public APIs |
| `make format` | ruff format + ruff check --fix | Whenever ruff complains; clears most issues |
| `make check` | lint + typecheck + test-unit | The single command before pushing |
| `make clean` | Deletes caches and build artefacts | When something's gone weird and you want a clean slate |

## TL;DR

Make is a task runner. The Makefile is a list of named recipes (`target: \n\t<commands>`). Sentinel uses it to give long, repeated shell commands short, memorable names, and to make `make check` mean the same thing locally and in CI. None of the targets do anything clever; they're all just wrappers around `uv run <something>` or `docker compose <something>`.

## Interview-style questions

1. What's the difference between `make dev` and running `docker compose up -d && uv run uvicorn ...` yourself?
2. Why does every target in the Sentinel Makefile appear in the `.PHONY` line?
3. The `check` target has no shell commands under it. How does it still do anything?
4. The Makefile has the indentation rule "must be tabs, not spaces." Why is this true, and what error would you get if you used spaces?
5. Could you remove the Makefile entirely without losing functionality? What would you lose vs what you'd keep?
6. The `dev` target depends on `up` and `migrate`. What happens if `migrate` fails? What happens if `up` was already running?
