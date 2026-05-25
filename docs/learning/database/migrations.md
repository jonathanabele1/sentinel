# Database migrations and Alembic

## What it is

### The 30-second pitch

**Alembic is a tool. Its job: keep your database schema in sync with your code as both evolve.**

If you change your code to say "users have an `email_verified` column," you can't just deploy the new code. The database still has the old `users` table without that column. *Something* has to add the column to the database. Alembic is that something. Each act of doing this is called **running a migration**.

### Physically, Alembic is three things

1. **A CLI tool** you invoke: `alembic upgrade head`, `alembic revision`, etc.
2. **A folder in your repo** (`migrations/`) containing one Python file per schema change. Each file has an `upgrade()` function and a `downgrade()` function. Forward and back buttons for your schema.
3. **A bookkeeping table** in your database called `alembic_version` holding exactly one row: which migration this database is currently at.

That's the whole tool. Code, files, bookkeeping table.

### The mental model: git for your database schema

| Git | Alembic |
| --- | --- |
| Commits | Migrations |
| Each commit has a unique hash | Each migration has a unique ID (`0001_initial`) |
| Each commit links to its parent | Each migration links to its `down_revision` |
| `HEAD` is the latest commit | `head` is the latest migration in the chain |
| `git log` shows history | `alembic history` shows history |
| `git checkout` moves between commits | `alembic upgrade` / `downgrade` moves between schema versions |

Each environment (your laptop, CI, production) is independently "at" some migration. `make migrate` walks whichever environment you're pointed at up to the latest.

### Where Alembic sits among similar tools

| Stack | Migration tool |
| --- | --- |
| **Python + SQLAlchemy** | **Alembic** |
| Python + Django | Django Migrations (built in) |
| Ruby on Rails | ActiveRecord Migrations (built in) |
| Java | Flyway, Liquibase |
| Go | Goose, golang-migrate, atlas |
| Node + Prisma | Prisma Migrate |
| .NET | Entity Framework Migrations |

They all do the same job: turn schema changes into versioned files applied in order. Sentinel uses SQLAlchemy as its ORM, and Alembic is written by SQLAlchemy's author with tight integration.

### What `make migrate` means in plain English

> "Look in `migrations/versions/`. Find any migrations that haven't been applied yet (compared to `alembic_version`). Apply them in order, inside transactions. Update `alembic_version` after each."

That is the entire command in one sentence.

### The repeated mental model

```
Code (Python models)  ←→  Migrations (versioned diffs in git)  ←→  Database (actual tables)
                            ↑                                          ↑
                            files in migrations/versions/              alembic_version table
                            tracked by git                             tracked by Alembic
```

Migrations are the bridge in the middle. They turn "I changed a model in code" into "the database now matches" through versioned, reviewable, reversible files.

## Why migrations exist

Without migrations, every database change becomes coordination overhead:

- Developer A creates a table. They ship code, then someone has to manually `CREATE TABLE` on prod.
- Developer B adds a column locally. They forget to tell anyone. The next deploy crashes when prod code expects a column the prod schema doesn't have.
- New contributors clone the repo and ask in Slack: "how do I get my local database into the right shape?"
- Schema drift between dev, staging, and prod becomes inevitable.

With migrations, every change is a file in `migrations/versions/` named with a unique ID. To bring any environment to the latest schema, you run `make migrate`. Done.

| Without migrations | With Alembic |
| --- | --- |
| Hand-type SQL on prod when something breaks | Migration file in git, applied identically everywhere |
| New contributor: "and then run these 47 ad-hoc SQL commands" | New contributor: `make migrate` |
| No way to undo a bad change | `alembic downgrade -1` reverses the last migration |
| Schema drift between dev and prod | CI runs the same migrations as prod, drift impossible |
| Auto-create tables from ORM models at startup | Schema is explicit, reviewable, versioned in git |

## How `make migrate` works in Sentinel

The command chain:

```
make migrate
   ↓
uv run alembic upgrade head
   ↓
alembic reads alembic.ini
   ↓
alembic runs migrations/env.py
   ↓
env.py connects to Postgres using settings.database_url
   ↓
alembic checks the alembic_version table
   ↓
finds nothing applied yet → target is "head" (latest migration)
   ↓
runs every pending migration's upgrade() function in order
   ↓
records each in alembic_version as it commits
```

### Step 1: `make migrate` is a one-line alias

```make
migrate:
	uv run alembic upgrade head
```

No magic. Just a short name for a longer command.

### Step 2: `uv run` puts you inside the venv

`uv run X` ensures `.venv/` is in sync with `uv.lock`, then runs `X` using the venv's Python and dependencies. Alembic is installed because it's listed in `pyproject.toml`'s `dependencies`.

### Step 3: Alembic boots from `alembic.ini`

Alembic's CLI reads `alembic.ini` first. The line that matters:

```ini
[alembic]
script_location = migrations
```

Tells Alembic that migration files live in `migrations/`. The rest of `alembic.ini` is mostly logging config.

### Step 4: `migrations/env.py` is the project-specific wiring

This file is the bridge between Alembic's generic machinery and your application's specifics:

```python
from apps.api.config import get_settings        # Load our Settings class
from packages.core.models.db import Base        # Load SQLAlchemy models

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata
```

The four things it does:

1. **Imports Settings.** Same `Settings` class the API uses, so dev/test/prod come from the same code path. The DB URL is in `.env`.
2. **Imports `Base`.** SQLAlchemy's `DeclarativeBase` that every ORM model inherits from. `Base.metadata` is the in-code registry of every table.
3. **Sets the connection URL.** Alembic doesn't hardcode connections; it gets the URL each run from your Settings.
4. **Sets `target_metadata`.** This is what enables `alembic revision --autogenerate` to diff code-vs-database later.

Then `env.py` calls `run_migrations_online()` which creates an async SQLAlchemy engine, opens a connection, and hands it to Alembic.

### Step 5: Alembic checks `alembic_version`

Alembic maintains a tiny table called `alembic_version` (one column, `version_num`, holding at most one row). Its purpose: record which migration the database is currently at.

First time you run `make migrate`, that table doesn't exist. Alembic creates it automatically:

```sql
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
```

Reads the row (none yet). Concludes: this database is at version "nothing."

### Step 6: Alembic builds the migration chain

It scans every file in `migrations/versions/` and looks at two fields in each:

```python
revision: str = "0001_initial"
down_revision: str | None = None
```

- `revision` is this migration's unique ID.
- `down_revision` is the ID of the migration before this one. `None` means it's the first.

Migrations form a linked list. With just one migration, the list is:

```
None → 0001_initial
```

`head` is the latest migration in the chain.

### Step 7: Compute the path and apply

Current version: `nothing`. Target: `head` (= `0001_initial`). Pending migrations: just `0001_initial`.

For each pending migration:

1. Begin a transaction.
2. Run the migration's `upgrade()` function.
3. Update `alembic_version` to record the new state.
4. Commit.

Postgres's transactional DDL means: if anything fails inside `upgrade()`, the entire migration rolls back. Either it all applied or none of it did. The database is never left in a half-migrated state.

For Sentinel's `0001_initial`, `upgrade()` did:

```python
op.create_table("review_runs", ...11 columns...)
op.create_index("ix_review_runs_repo_pr", ...)
op.create_index("ix_review_runs_status", ...)
op.create_index("ix_review_runs_started_at", ...)
op.create_table("step_executions", ...11 columns incl JSONB...)
op.create_index("ix_step_executions_run_id", ...)
op.create_index("ix_step_executions_step_name", ...)
```

Then:

```sql
INSERT INTO alembic_version (version_num) VALUES ('0001_initial');
```

Commit.

## What ends up in the database

After a fresh `make migrate`:

```
sentinel database
└── public schema
    ├── alembic_version          1 row: "0001_initial"
    ├── review_runs              0 rows; 11 columns; 3 indexes
    └── step_executions          0 rows; 11 columns; 2 indexes; FK to review_runs(id)
```

You can see this directly in DBeaver, or via `psql`:

```bash
docker compose -f infra/docker-compose.yml exec postgres psql -U sentinel -c "\dt"
```

## Anatomy of a migration file

A migration file (Sentinel's first one is `migrations/versions/20260525_1200_initial.py`) has four required pieces and two functions:

```python
revision: str = "0001_initial"          # this migration's ID
down_revision: str | None = None         # ID of the previous migration (None = first)
branch_labels: ...                       # rarely used; for multi-branch projects
depends_on: ...                          # rarely used; for cross-database dependencies


def upgrade() -> None:
    """Apply this migration forward."""
    op.create_table(...)
    op.create_index(...)


def downgrade() -> None:
    """Reverse this migration."""
    op.drop_index(...)
    op.drop_table(...)
```

`op` is Alembic's API. `op.create_table()`, `op.add_column()`, `op.drop_index()`, `op.alter_column()`, `op.execute("UPDATE ...")` etc. Alembic generates the right DDL for the database flavour you're using (Postgres in our case).

`downgrade()` is what makes `alembic downgrade -1` work. Writing a correct `downgrade()` is harder than it looks (data migrations are usually one-way), so for production-grade projects, the convention is "always provide one but never trust it for prod rollback alone."

## Common Alembic commands

```bash
# Apply all pending migrations.
uv run alembic upgrade head

# Apply migrations up to a specific version.
uv run alembic upgrade 0003_some_id

# Step forward one migration.
uv run alembic upgrade +1

# Step back one migration.
uv run alembic downgrade -1

# Roll back to before any migrations were applied.
uv run alembic downgrade base

# Show the current version of the connected DB.
uv run alembic current

# Show the full history of migrations.
uv run alembic history --verbose

# Generate a new migration by diffing models against the DB (autogenerate).
uv run alembic revision --autogenerate -m "add review_findings table"

# Generate an empty migration to hand-write.
uv run alembic revision -m "data backfill for region_code"
```

## Generating new migrations: the autogenerate flow

When you change a model in `packages/core/models/db.py`, you generate a new migration:

```bash
uv run alembic revision --autogenerate -m "add review_findings table"
```

What happens:

1. Alembic connects to the database.
2. It loads `Base.metadata` (the in-code schema declaration).
3. It introspects the database's actual schema.
4. It diffs the two. Whatever's in `Base.metadata` but not in the DB becomes the `upgrade()`. Whatever's in the DB but not in `Base.metadata` becomes the `downgrade()`.
5. It writes a new migration file to `migrations/versions/`.

**Critical:** always review the generated file. Autogenerate is imperfect:

- It catches new tables and columns reliably.
- It catches dropped tables and columns reliably.
- It often misses constraint changes, server defaults, and check constraints.
- It cannot detect renames (a renamed column looks like "drop old, add new" to autogenerate, which is data-destructive).

Sentinel's discipline: every generated migration is read end-to-end, hand-corrected if needed, and committed alongside the model change.

## Why this matters for Sentinel specifically

Sentinel's design leans on Postgres in a specific way: the **JSONB columns** on `step_executions` are how replayability works. Every step's full inputs and outputs are stored there as JSON. The replay endpoint reads those columns to reconstruct a past run.

Migrations are what guarantee those columns exist with the right type (JSONB, not TEXT) in every environment. If dev had JSONB but prod had TEXT, replay would silently corrupt: the writes would succeed but reads would parse strings as strings instead of JSON.

Looking ahead in the build plan:

- **Week 4** adds `review_findings` (a new table). New migration.
- **Week 6** adds a `prompt_version` field to cache rows. New migration.
- **Week 7** adds rate-limit tracking columns. New migration.

By the time Sentinel is finished, the `migrations/versions/` folder is itself part of the portfolio signal: it shows you ship database changes the way production teams do, atomically, in git, reversibly.

## Common confusions

**"Why not just `CREATE TABLE IF NOT EXISTS` at app startup?"** That's the "auto-create from models" approach. It works for greenfield demos but falls apart the moment you need to change a schema. There's no record of what changed when, no way to apply changes to existing data, no rollback story. It also can't add columns to existing tables, because `CREATE TABLE` doesn't fire if the table exists.

**"Should `downgrade()` actually be tested?"** For dev: yes, occasionally, so you don't paint yourself into a corner. For prod: rolling forward through a corrective migration is usually safer than rolling back. Treat `downgrade()` as a courtesy, not a recovery strategy.

**"Why don't we use the SQLAlchemy `Base.metadata.create_all()`?"** It's the "auto-create" pattern above. Fine for unit tests with a throwaway database. Wrong for any environment you care about.

**"What if the autogenerated migration is wrong?"** Edit it. Migration files are normal Python; you can rewrite the `upgrade()` and `downgrade()` to whatever you actually want.

## TL;DR

Migrations are versioned schema changes stored in git. Alembic is the migration tool. `make migrate` runs `alembic upgrade head`, which: reads `alembic.ini`, runs `migrations/env.py` (which connects to Postgres using your Settings), checks the `alembic_version` table to see which migrations have been applied, and runs each pending migration's `upgrade()` function inside a transaction. The migration system is what guarantees every environment (your laptop, CI, production) ends up with an identical schema. Generate new migrations by editing your SQLAlchemy models and running `alembic revision --autogenerate -m "..."`; always review the result.

## Interview-style questions

1. Walk through what happens between `make migrate` and the SQL being executed in Postgres. Name the files involved.
2. What does the `alembic_version` table contain, and how does Alembic use it?
3. What does `head` mean in `alembic upgrade head`? When would you use a specific revision ID instead?
4. Why is wrapping each migration in a transaction important? What guarantees does Postgres's transactional DDL give you?
5. Autogenerate produces a migration that says "drop column `email`, add column `email_address`." What's wrong with this and how would you fix it?
6. You discover production was running on an older migration than dev when a bug surfaced. Walk through how you'd diagnose, what data you'd want, and what the recovery plan looks like.
7. Why does Sentinel's design specifically depend on JSONB columns being typed correctly across environments? What would happen if dev had JSONB and prod had TEXT?
