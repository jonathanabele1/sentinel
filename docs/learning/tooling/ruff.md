# Ruff

## What it is

`ruff` is a Python linter and formatter written in Rust. It does two distinct jobs in one tool:

1. **Lint:** read your code, flag problems (unused imports, type errors that style checkers can catch, security smells, possible bugs).
2. **Format:** rewrite your code to canonical style (line wraps, quote style, import order).

Ruff replaces a stack of older Python tools that used to do each piece separately: `flake8` (linting), `isort` (import sorting), `black` (formatting), `pyupgrade` (modernising syntax), `bandit` (security), `pydocstyle` (docstrings), and a dozen others. One Rust binary, dramatically faster than any of those individually.

## What it does

### Linting

`uv run ruff check .` walks every Python file under `.`, applies a configured set of rules, and prints violations like this:

```
F401 [*] `pydantic.Field` imported but unused
  --> apps/api/config.py:9:22
   |
 7 | from typing import Literal
 8 |
 9 | from pydantic import Field
   |                      ^^^^^
```

Each violation has a **rule code** (`F401`), a **message**, and optionally a **`[*]` marker** that means "ruff can auto-fix this." Run `ruff check --fix` and it rewrites the file to clear all auto-fixable issues. Whatever's left over is for you to handle manually.

### Formatting

`uv run ruff format .` does cosmetic rewrites: line lengths, indentation, trailing commas, blank lines around top-level defs, etc. This is the same job `black` used to do, but ruff's formatter is now compatible enough that black-shaped people are mostly happy.

### The `make format` combo

Our Makefile target chains them:

```make
format:
	uv run ruff format .
	uv run ruff check --fix .
```

Run that and 99% of complaints clear in one go.

## Rule families and what they mean

Ruff groups rules into "selectors" identified by a letter prefix. Open `pyproject.toml` and look at `[tool.ruff.lint].select`:

```toml
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes
    "I",    # isort
    "B",    # flake8-bugbear
    "C4",   # flake8-comprehensions
    "UP",   # pyupgrade
    "S",    # flake8-bandit (security)
    "SIM",  # flake8-simplify
    "RUF",  # ruff-specific
]
```

| Prefix | Family | What it catches |
| --- | --- | --- |
| `E`, `W` | pycodestyle | PEP 8 style: indentation, line length, blank lines. Mostly handled by the formatter now. |
| `F` | pyflakes | Real bugs: unused imports, undefined names, redefined functions. |
| `I` | isort | Import sorting (stdlib → third-party → first-party, alphabetical inside each group). |
| `B` | flake8-bugbear | Likely bugs: mutable default args, bare `except`, loop variables captured in closures. |
| `C4` | flake8-comprehensions | Inefficient list/dict/set comprehensions. |
| `UP` | pyupgrade | Outdated syntax: `Union[X, Y]` → `X \| Y`, `Optional[X]` → `X \| None`, `timezone.utc` → `UTC`. |
| `S` | flake8-bandit | Security: hardcoded passwords, `eval`, `pickle`, weak crypto, binding to 0.0.0.0. |
| `SIM` | flake8-simplify | Simplifications: redundant `if` branches, `if x == True`. |
| `RUF` | ruff-specific | Ruff's own opinions: unused noqa directives, mutable class attrs without `ClassVar`. |

You compose your project's rule set by listing prefixes. You can disable individual rules within a family via `ignore = [...]`.

## Rules Sentinel has hit so far

Worth memorising the codes you've seen because you'll see them again.

### `F401` — Unused import

```
from pydantic import Field   # but Field is never used
```

The name is imported but never referenced. Dead code; ruff removes it on `--fix`.

### `I001` — Imports not sorted

The import block is grouped or ordered wrong. Stdlib should come first, then third-party, then first-party (your own code). Within each group, alphabetical. Auto-fixable.

### `UP017` — Use `datetime.UTC` alias

```python
from datetime import datetime, timezone
datetime.now(timezone.utc)    # old style
```

Python 3.11+ has `datetime.UTC` as a shorter alias. Ruff nudges you to:

```python
from datetime import UTC, datetime
datetime.now(UTC)
```

Auto-fixable.

### `S104` — Possible binding to all interfaces

```python
api_host: str = "0.0.0.0"
```

Bandit security rule: `0.0.0.0` means "bind to every network interface," which exposes the service externally. Real warning in production. For a dev server reachable from Docker or ngrok, it's correct, so we suppress with a per-line `noqa`:

```python
api_host: str = "0.0.0.0"  # noqa: S104
```

Not auto-fixable (it's a judgment call, not a typo).

### `BLE001` — Blind except

```python
try:
    risky()
except Exception:    # ruff complains
    ...
```

Catching `Exception` hides real bugs. The fix is usually to narrow to a specific exception type. Sometimes (like in our webhook handler, where a failed comment post shouldn't fail the whole webhook ack) the blind catch is intentional, and you suppress with `# noqa: BLE001` and a comment explaining why.

Note: BLE001 was *not* enabled in our config when we first saw it. Ruff caught a leftover suppression for a non-enabled rule via:

### `RUF100` — Unused noqa directive

```python
except Exception as exc:  # noqa: BLE001
```

If BLE001 isn't actually enabled, the suppression is dead weight, and ruff flags it. Either enable the rule or delete the suppression. Auto-fixable (it deletes the suppression).

## Suppressing rules

Three escape hatches, in order of preference:

1. **Per-line `# noqa: <code>`** — suppress one rule on one line. Always include the code so the suppression is targeted; bare `# noqa` is itself flagged.
2. **Per-file ignores** in `pyproject.toml`:
   ```toml
   [tool.ruff.lint.per-file-ignores]
   "tests/**/*.py" = ["S105", "S106"]   # hardcoded passwords ok in tests
   ```
3. **Global `ignore`** in `pyproject.toml` — turns the rule off everywhere. Last resort.

The discipline: every `noqa` should have a one-line comment explaining *why* the rule is being suppressed. If you can't write the comment, you probably shouldn't suppress it.

## Why we use it

1. **One tool, one config, one binary.** No more juggling `flake8 + isort + black + pyupgrade + bandit` with their separate configs and version constraints.
2. **Speed.** Ruff lints the entire Sentinel codebase in tens of milliseconds. Old stack would take seconds.
3. **Consistency without arguments.** Ruff format decides the questions ("should this import be wrapped?", "double or single quotes?") so nobody has to.
4. **Real bug catching.** The `F`, `B`, `S` rule families catch actual problems, not just style.

## Common commands

```bash
# Lint everything.
uv run ruff check .

# Lint and auto-fix what's fixable.
uv run ruff check --fix .

# Format (rewrites whitespace, wraps long lines, etc).
uv run ruff format .

# Check format without writing (CI uses this).
uv run ruff format --check .

# Lint one file.
uv run ruff check apps/api/main.py

# Show what a specific rule does.
uv run ruff rule S104

# Sentinel's combined shortcut.
make format
```

## TL;DR

Ruff is a Rust-based replacement for the entire Python lint+format ecosystem. `ruff check` finds problems; `ruff format` rewrites style. Most violations have a `[*]` marker meaning ruff can fix them automatically. `make format` runs both in sequence and clears most issues. Rules are organised by letter prefix (`F`=bugs, `I`=imports, `S`=security, etc.); the active set is configured in `pyproject.toml`. Suppress individual lines with `# noqa: <code>` and a comment explaining why.

## Interview-style questions

1. What's the difference between `ruff check` and `ruff format`? Why does ruff do both jobs in one tool when older stacks split them?
2. The `[*]` marker after a rule code means what? What rule families typically *cannot* be auto-fixed?
3. We have `api_host: str = "0.0.0.0"  # noqa: S104` in `config.py`. Walk through what S104 catches, why we're suppressing it, and when we *wouldn't* be.
4. Why does ruff have a rule (RUF100) specifically for dead `noqa` directives?
5. The CI runs `ruff format --check` instead of `ruff format`. What's the difference and why does CI want the check version?
6. List five tools ruff replaces and what each one used to do.
