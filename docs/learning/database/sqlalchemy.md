# SQLAlchemy

## What it is

**SQLAlchemy is Python's most popular toolkit for working with databases.** It lets you talk to Postgres (or MySQL, SQLite, etc.) from Python using two flavours:

1. **The ORM (Object-Relational Mapper):** define Python classes that map to database tables; create, read, update, delete rows by working with Python objects, not raw SQL.
2. **Core (the SQL expression language):** build SQL queries using Python expressions when the ORM is too high-level for what you need.

Most apps use the ORM by default and drop down to Core for performance-critical or unusual queries. Sentinel follows this pattern.

## What is an ORM

Without an ORM, talking to a database from Python looks like this:

```python
import asyncpg

conn = await asyncpg.connect("postgresql://...")
rows = await conn.fetch(
    "SELECT id, pr_url, status FROM review_runs WHERE repo_full_name = $1 LIMIT 10",
    "acme/api",
)
for row in rows:
    print(row["pr_url"], row["status"])
```

You write SQL strings. You handle tuples and dicts. If you change a column name in the database, you grep your codebase and hope you find every reference.

With an ORM, the same operation:

```python
from sqlalchemy import select

result = await session.execute(
    select(ReviewRun).where(ReviewRun.repo_full_name == "acme/api").limit(10)
)
for run in result.scalars():
    print(run.pr_url, run.status)
```

`ReviewRun` is a Python class. `ReviewRun.repo_full_name` is a typed attribute. Rename the column in the model and your IDE and mypy catch every place that broke at edit time, not at runtime.

The ORM maps:

- **Python classes** ↔ **database tables**
- **Python attributes** ↔ **table columns**
- **Python objects** ↔ **rows**

And generates the SQL behind the scenes.

## Sentinel's actual SQLAlchemy code

Open `packages/core/models/db.py`:

```python
class Base(DeclarativeBase):
    """Shared declarative base. Alembic discovers tables via Base.metadata."""


class ReviewRun(Base):
    __tablename__ = "review_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pr_url: Mapped[str] = mapped_column(String(512), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    pr_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    plan_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    ...

    steps: Mapped[list[StepExecution]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
```

Read line by line:

- `class ReviewRun(Base):` — declare a Python class. Inheriting from `Base` (a `DeclarativeBase`) tells SQLAlchemy "this is a model and its table belongs in the registry."
- `__tablename__ = "review_runs"` — the table in Postgres this class corresponds to.
- `id: Mapped[uuid.UUID]` — Python type annotation (so mypy and your IDE understand `run.id` is a UUID).
- `mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)` — describes the SQL column: type `UUID`, primary key, default to a fresh UUID4 if not provided.
- `relationship(back_populates="run", ...)` — declares the link from `ReviewRun.steps` to all the `StepExecution` rows that reference it. Lets you write `run.steps` in Python instead of joining manually.

## How you use a SQLAlchemy model

### Inserting a row

```python
run = ReviewRun(
    pr_url="https://github.com/acme/api/pull/482",
    repo_full_name="acme/api",
    pr_number=482,
    head_sha="abc123",
    plan_name="placeholder",
    status="completed",
)
session.add(run)
await session.commit()
```

This is what the webhook handler does. `session.add(run)` puts the object in the session's pending queue; `await session.commit()` flushes the pending changes as INSERTs and commits the transaction. SQLAlchemy generates:

```sql
INSERT INTO review_runs (id, pr_url, repo_full_name, pr_number, head_sha, plan_name, status, ...)
VALUES (...);
```

You never wrote the SQL.

### Reading rows

```python
from sqlalchemy import select

# All runs for a repo
result = await session.execute(
    select(ReviewRun).where(ReviewRun.repo_full_name == "acme/api")
)
runs = result.scalars().all()

# One run by id
result = await session.execute(
    select(ReviewRun).where(ReviewRun.id == some_uuid)
)
run = result.scalar_one_or_none()
```

`select(ReviewRun).where(...)` is the SQLAlchemy 2.0 query syntax. It's a Python expression tree that compiles to SQL when executed.

### Updating a row

```python
run = await session.get(ReviewRun, some_uuid)
run.status = "failed"
run.error = "LLM provider returned 500"
await session.commit()
```

Get the object, mutate its attributes, commit. SQLAlchemy diffs the changes and emits a single UPDATE statement.

### Deleting a row

```python
run = await session.get(ReviewRun, some_uuid)
await session.delete(run)
await session.commit()
```

## SQLAlchemy is not a driver

A common confusion: SQLAlchemy is **not** the library that physically talks to Postgres. It sits one layer above that.

```
Your Python code
    ↓
SQLAlchemy ORM         ← high-level abstraction
    ↓
SQLAlchemy Core        ← SQL expression language
    ↓
asyncpg                ← the actual driver (speaks Postgres wire protocol)
    ↓
Postgres server (over TCP)
```

A **driver** is the library that implements the database's wire protocol. Postgres has its own binary protocol over TCP; the driver is what knows how to encode queries into that protocol and decode the responses. For Postgres in Python the two common drivers are:

- **asyncpg** — modern async, fast, written in Cython. What Sentinel uses.
- **psycopg** (`psycopg2` / `psycopg3`) — older, sync by default, historical standard.

You always need a driver. SQLAlchemy uses it under the hood. In `pyproject.toml`:

```toml
dependencies = [
    "sqlalchemy[asyncio]>=2.0.36",   # the ORM + query builder
    "asyncpg>=0.30.0",               # the actual Postgres driver
]
```

SQLAlchemy reads your `database_url` (`postgresql+asyncpg://...`), notices the `+asyncpg`, and routes its queries through asyncpg to reach Postgres.

You could skip SQLAlchemy entirely and use asyncpg directly:

```python
import asyncpg

conn = await asyncpg.connect("postgresql://sentinel:sentinel@localhost:5432/sentinel")
rows = await conn.fetch("SELECT * FROM review_runs LIMIT 10")
```

That works. You're choosing to write the SQL by hand and handle rows as dicts, with no type safety and no migrations integration.

### MongoDB analogy

Confusing PyMongo with SQLAlchemy is common. They are at different layers:

| Postgres world | MongoDB world | Layer |
| --- | --- | --- |
| `asyncpg` / `psycopg` | `PyMongo` / `Motor` | **Driver:** wire protocol |
| `SQLAlchemy Core` | (PyMongo's query syntax is already Python-native) | **Query builder** |
| `SQLAlchemy ORM` | `Beanie`, `MongoEngine`, `ODMantic` | **ORM / ODM:** classes ↔ rows/documents |
| `Alembic` | (no direct equivalent; MongoDB schemas are flexible) | **Schema versioning** |

PyMongo and asyncpg are peers. SQLAlchemy and Beanie/MongoEngine are peers, one layer up. ODMs are less common in MongoDB land because documents are already dict-shaped; the value-add of an extra layer is smaller there than it is for relational SQL.

## The two layers (within SQLAlchemy itself)

### ORM (high-level)

What you saw above. Define classes, work with objects. SQLAlchemy turns method calls into SQL. Used for almost all application code.

### Core (mid-level)

For queries the ORM doesn't express cleanly, drop to Core:

```python
from sqlalchemy import select, func

stmt = (
    select(
        ReviewRun.repo_full_name,
        func.count().label("total"),
        func.sum(ReviewRun.cost_cents).label("cost"),
    )
    .group_by(ReviewRun.repo_full_name)
    .order_by(func.count().desc())
)
result = await session.execute(stmt)
for row in result.all():
    print(row.repo_full_name, row.total, row.cost)
```

Core lets you build any SQL the database supports. The expression objects (`select`, `func`, `case`, etc.) are still Python, so you get type checking and composability, but you're closer to the SQL than the ORM lets you get.

### Raw SQL (low-level escape hatch)

When even Core is in the way:

```python
result = await session.execute(
    text("SELECT * FROM review_runs WHERE pr_url ILIKE :pattern"),
    {"pattern": "%acme%"},
)
```

Parameterised queries; SQLAlchemy still handles the connection and parameter binding. You almost never need this for Sentinel.

## How SQLAlchemy fits with the other pieces

```
┌──────────────────────────────────────────────────────────────────────────┐
│ SQLAlchemy ORM                                                           │
│   Python classes (ReviewRun, StepExecution) ↔ database tables.           │
│   You read/write rows by working with Python objects.                    │
│                                                                          │
│ SQLAlchemy Core                                                          │
│   Python expressions that compile to SQL. Used for advanced queries.     │
│                                                                          │
│ asyncpg                                                                  │
│   The actual Postgres driver. SQLAlchemy uses this under the hood to     │
│   send queries over the network.                                         │
│                                                                          │
│ Alembic                                                                  │
│   Migration tool. Compares SQLAlchemy's Base.metadata (the in-code       │
│   schema) to the database to generate/apply changes.                     │
└──────────────────────────────────────────────────────────────────────────┘
```

When you ran `make migrate`, Alembic used `Base.metadata` (populated by your SQLAlchemy models) to figure out what tables to create. The schema *is* the SQLAlchemy classes; Alembic is the deployment mechanism.

## Sessions and engines, briefly

Two more SQLAlchemy concepts you'll see in `packages/core/models/session.py`:

```python
def make_engine(**kwargs):
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
    )

def get_session_factory():
    return async_sessionmaker(get_engine(), expire_on_commit=False)

async def get_session():
    factory = get_session_factory()
    async with factory() as session:
        yield session
```

- **Engine:** a long-lived object representing the database connection pool. Created once at app startup. You don't usually interact with it directly.
- **Session:** a short-lived object representing a unit of work. Owns a connection from the pool, tracks the objects you've loaded or modified, and commits or rolls back a transaction when you tell it to.

In our FastAPI app, `get_session` is wired as a dependency: every request gets its own session, which is committed (or rolled back on error) and returned to the pool when the request ends. This keeps connections cheap and transactions isolated.

## Where SQLAlchemy sits among alternatives

| Tool | Type | Notes |
| --- | --- | --- |
| **SQLAlchemy** | ORM + query builder | Most popular Python option. Mature, flexible, async-first since 2.0. |
| Django ORM | ORM | Built into Django. Tightly coupled; only used in Django projects. |
| Tortoise ORM | Async ORM | Lighter, Django-inspired API. Smaller ecosystem. |
| SQLModel | ORM | SQLAlchemy + Pydantic. By the FastAPI author. Less escape hatch when you need raw SQLAlchemy. |
| Peewee | ORM | Small, simple. Hobby projects. |
| asyncpg directly | Driver | No ORM, just SQL. Max performance, max work. |

Sentinel chose SQLAlchemy because:

1. It's the canonical Python ORM; the job-market signal is real.
2. Async support is first-class since 2.0.
3. Alembic is its blessed migration tool.
4. It scales from trivial CRUD to complex multi-table joins without forcing a rewrite.

## Why we use it

Five reasons:

1. **Type safety.** `run.pr_url` is known to be a `str`. Typo `run.pr_urll` and your IDE flags it.
2. **Refactoring safety.** Rename a column in the model, mypy tells you every place that broke.
3. **Database portability.** The same model code works against Postgres, MySQL, SQLite. (You don't usually switch, but you can.)
4. **Less boilerplate.** No more `cursor.execute("INSERT INTO ... VALUES (?, ?, ?)", ...)` scattered across the codebase.
5. **Migrations integrate naturally.** Alembic uses the same model definitions to generate schema diffs.

## TL;DR

SQLAlchemy is Python's main database toolkit. The ORM (object-relational mapper) maps Python classes to database tables: `ReviewRun` the class ↔ `review_runs` the table; `run.pr_url` ↔ a `pr_url` column. You write Python; SQLAlchemy generates SQL. The Core layer is a SQL expression language for queries the ORM doesn't express cleanly. Sessions are short-lived units of work; engines are long-lived connection pools. Alembic uses your SQLAlchemy model definitions to generate migrations. The two together (SQLAlchemy + Alembic) are the canonical Python data-access stack.

## Interview-style questions

1. What's the difference between SQLAlchemy's ORM and Core? When would you use one over the other?
2. Walk through what `session.add(run)` and `await session.commit()` actually do under the hood. What SQL gets generated?
3. Explain the role of `Base.metadata`. How does Alembic use it?
4. Why does Sentinel use `async_sessionmaker` and `create_async_engine` instead of the sync equivalents?
5. The `relationship(back_populates="run", cascade="all, delete-orphan")` line on `ReviewRun.steps` does what? What happens when you delete a `ReviewRun`?
6. You have a query that ORM `select(...)` makes 100x slower than the raw SQL equivalent. Walk through how you'd diagnose and fix it.
7. When would you choose SQLModel or Tortoise ORM over SQLAlchemy?
