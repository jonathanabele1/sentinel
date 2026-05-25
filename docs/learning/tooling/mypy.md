# mypy

## What it is

`mypy` is Python's most established **static type checker**. It reads the type annotations in your code and verifies that the types you declared are consistent with how the code actually uses them. It doesn't run your program; it just analyses the source.

Python is dynamically typed at runtime, so type hints are documentation, not enforcement. Mypy turns that documentation into a contract: if you wrote `def foo(x: int) -> str`, mypy checks that every caller passes an `int` and every `return` returns a `str`. Bugs that would otherwise show up at 3am in production show up at edit time.

## What it does

```python
def double(x: int) -> int:
    return x * 2

double("hello")    # mypy: Argument 1 to "double" has incompatible type "str"; expected "int"
```

Mypy walks the AST of every file, builds a type for every expression, and flags inconsistencies. The output looks like:

```
apps/api/routes/webhooks.py:69: error: "warning" of "BoundLogger" gets multiple values for keyword argument "event"  [misc]
packages/core/observability/logging.py:62: error: Returning Any from function declared to return "BoundLogger"  [no-any-return]
Found 3 errors in 2 files (checked 26 source files)
```

Each error has a **path:line**, a **message**, and an **error code** in brackets (`[no-any-return]`). You can search the code in mypy's docs to understand the rule.

## Strict mode

Mypy has a `--strict` flag that turns on a bunch of strictness checks. We configure these in `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.12"
strict = true
warn_unused_ignores = true
warn_redundant_casts = true
warn_return_any = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
no_implicit_optional = true
```

What strict mode buys you in practice:

| Setting | What it enforces |
| --- | --- |
| `disallow_untyped_defs` | Every function must have type annotations on parameters and return. No annotation-less functions allowed. |
| `disallow_incomplete_defs` | Either fully annotate a function or don't at all. No "annotated half the params" sloppiness. |
| `no_implicit_optional` | `def foo(x: int = None)` is an error. You must say `x: int | None = None` explicitly. |
| `warn_return_any` | If a function is annotated to return `BoundLogger` and the body returns something mypy only knows as `Any`, that's an error. |
| `warn_unused_ignores` | A `# type: ignore` that doesn't actually suppress anything is itself an error. Same idea as ruff's RUF100. |
| `warn_redundant_casts` | A `cast(int, x)` where `x` is already typed as `int` is dead code, flagged. |

For tests, we relax `disallow_untyped_defs` because test functions rarely benefit from typing every fixture. Look at the override:

```toml
[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
```

## Errors Sentinel has hit so far

Worth knowing what these messages mean because you'll see them again.

### `[no-any-return]` — Returning Any from a typed function

What we saw:

```
packages/core/observability/logging.py:62: error: Returning Any from function declared to return "BoundLogger"
```

The code:

```python
def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

The problem: `structlog.get_logger()` is typed in its stubs as returning `Any`. Returning `Any` from a function declared to return `BoundLogger` is a strictness violation under `warn_return_any`. The fix is to `cast` it:

```python
from typing import cast

def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
```

`cast` is mypy's "trust me" annotation. It generates no runtime code; it just tells mypy "treat this value as type X." Use it sparingly: every `cast` is a place where you're overruling the type checker, so if you're wrong you get a runtime crash mypy could have caught.

### `[misc]` — Multiple values for keyword argument

What we saw:

```
apps/api/routes/webhooks.py:69: error: "warning" of "BoundLogger" gets multiple values for keyword argument "event"
```

The code:

```python
log.warning(
    "webhook.bad_signature",
    event=x_github_event,
    delivery=x_github_delivery,
)
```

The problem: `BoundLogger.warning()` is typed as `warning(event: str, **kw)`. Mypy reads it as: the positional `"webhook.bad_signature"` binds to the parameter named `event`, and then `event=x_github_event` is a second value for the same parameter. Classic name collision.

The fix: rename our kwarg so it doesn't shadow the log-event parameter:

```python
log.warning(
    "webhook.bad_signature",
    gh_event=x_github_event,    # was: event=...
    delivery=x_github_delivery,
)
```

This is a useful lesson: structlog and stdlib logging both use "event" as the conventional name for the log message itself. Don't pass `event=` as a context kwarg or you'll collide with the function signature.

## Escape hatches

Two main ways to tell mypy "I know this looks wrong, trust me":

### `cast(X, value)`

```python
from typing import cast
result = cast("MyType", something_loosely_typed)
```

Generates no runtime code. Tells mypy "treat this value as MyType." Use when a third-party library's stubs are loosely typed but you know the actual type.

### `# type: ignore[error-code]`

```python
result = something_weird()  # type: ignore[no-any-return]
```

Suppresses one specific error on one line. Always include the error code in brackets (a bare `# type: ignore` is itself an error under `warn_unused_ignores`). Use when fixing the error properly would require restructuring code for a trivial gain.

The discipline is the same as with ruff `# noqa`: every suppression should have a comment explaining *why* it's needed.

## Why we use it

1. **Bugs caught at edit time.** Renaming a function and missing one caller, passing the wrong type, drifting between a function's signature and its implementation: mypy catches all of these before runtime.
2. **Documentation that can't lie.** Type annotations are checked, so the function signature is always accurate. No more comments saying "x is a list of dicts" that drifted from reality three refactors ago.
3. **Refactoring safety.** Want to change a function's return type? Mypy tells you every place that breaks. Massive win on a codebase you don't entirely fit in your head.
4. **It's table stakes for a portfolio project.** Strict mypy on every PR is the easy signal that you ship production code.

## Common confusions

**"Why do I have to type `List[int]` instead of `list[int]`?"** You don't, in Python 3.9+. We're on 3.12. `list[int]`, `dict[str, int]`, `int | None` are all native. The `typing.List` / `typing.Dict` / `typing.Optional` forms are legacy.

**"Why does mypy disagree with my IDE?"** Because IDEs often run a different checker (Pyright, in the case of VS Code's Pylance). They share most rules but diverge on edge cases. The one that matters is the one CI runs, which is mypy.

**"Mypy is slow."** It can be. There's a `dmypy` daemon mode for incremental rechecks: `dmypy run -- <args>`. For Sentinel's scale this isn't needed yet.

**"What about runtime type checking?"** Mypy is purely static. Pydantic does *runtime* validation, which is why we use Pydantic at every boundary (request bodies, settings, agent outputs). The two complement each other.

## Common commands

```bash
# Check the whole project (uses the `files` setting in pyproject.toml).
uv run mypy

# Check one file.
uv run mypy apps/api/main.py

# Check with extra verbosity.
uv run mypy --verbose

# Show the inferred type of an expression.
# Add `reveal_type(x)` to your code; mypy will print it on next run.
```

## TL;DR

Mypy is a static type checker. It reads your annotations and flags inconsistencies. Strict mode (configured in `pyproject.toml`) means every function must be fully typed and the type system can't be silently undermined by `Any`. When you genuinely need an escape hatch, use `cast(X, value)` or `# type: ignore[error-code]` with a one-line comment explaining why. The error codes you'll see most often are `[no-any-return]`, `[misc]`, `[arg-type]`, and `[return-value]`.

## Interview-style questions

1. What's the difference between Python's runtime types and what mypy checks? Why aren't they the same?
2. Walk through the `cast(X, value)` function. What does it generate at runtime? When should you use it vs `# type: ignore`?
3. Strict mode enables `warn_return_any`. Why is "returning Any from a typed function" a problem worth flagging?
4. When you saw "multiple values for keyword argument `event`," what was the actual cause? How would you avoid that whole class of bug?
5. Mypy + Pydantic together cover both static and runtime type validation. Where does each one fit?
6. Why do we relax `disallow_untyped_defs` for tests but not for the main codebase?
