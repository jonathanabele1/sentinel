# Learning notes

Self-contained explanations of the concepts, tools, and patterns used in Sentinel. Written so a reader (including future-me preparing for an interview) can pick up any single file cold without needing to read the rest.

Each file follows the same loose shape:

1. **What it is** — the one-paragraph mental model.
2. **What it does** — concrete behaviour, with examples.
3. **Why we use it** — what problem it solves, what we'd lose without it.
4. **TL;DR** — the soundbite to repeat back in an interview.

These are notes, not reference docs. For authoritative behaviour, the upstream documentation is always the right source.

## Index

### Docker

- [Docker Compose](docker/compose.md) — multi-container orchestration, what the YAML file declares, how `up` and `down` work.
- [Volumes](docker/volumes.md) — how data survives container restarts; named volumes vs bind mounts; what happens with `down -v`.
- [Why Docker (and the alternatives)](docker/why-docker.md) — is Docker required? Five real alternatives (Homebrew, cloud dev DBs, in-process fakes, process supervisors, Podman/OrbStack); why Docker wins as the default; the "containers as a concept" vs "Docker as a product" distinction.

### Python tooling

- [Overview](tooling/overview.md) — the five-tool stack (make, uv, ruff, mypy, pytest), how they layer.
- [Makefile](tooling/makefile.md) — task runner basics, every target in the Sentinel Makefile explained.
- [uv](tooling/uv.md) — Python package manager + project runner; what it replaces, lockfiles, `uv run`.
- [ruff](tooling/ruff.md) — linter + formatter; rule families, the specific rules Sentinel has hit, suppression patterns.
- [mypy](tooling/mypy.md) — static type checker; strict mode, error codes seen, `cast()` and `# type: ignore`.

### Observability

- [Jaeger and distributed tracing](observability/jaeger.md) — what a trace is, spans, OpenTelemetry pipeline, how Sentinel emits and views them.

### Database

- [SQLAlchemy](database/sqlalchemy.md) — the ORM concept; Python classes ↔ tables, how Sentinel's `ReviewRun` and `StepExecution` work, ORM vs Core, sessions and engines, how it pairs with Alembic.
- [Migrations and Alembic](database/migrations.md) — what a migration is, what `make migrate` does layer-by-layer, the `alembic_version` table, autogenerate flow, why this matters for Sentinel's JSONB-dependent design.

### Concepts

- [Local dev vs production](concepts/local-vs-production.md) — why Sentinel runs Postgres in a container locally but would use RDS in production; the env-var seam that makes the code identical.

### (Coming as we go)

- pytest (will write up when we hit our first interesting test pattern)
- structlog and JSON logging in production
- Prometheus metrics: counters vs histograms, naming conventions
- GitHub integration: GitHub Apps vs OAuth, webhooks, HMAC validation
- Architecture: deterministic orchestration, replayability via JSONB snapshots, calibration and Brier score
- LLM patterns: tool use for structured outputs, retries on validation failure, semantic vs exact caching

Each new explanation added during development gets a heading in this index plus a file under the right subfolder.

## How to use these for interview prep

A reasonable study loop:

1. Read one file end-to-end.
2. Cover the TL;DR and try to recite it.
3. Open the file in an AI chat and ask: "generate 8 interview questions about this, ranging from definitional to design-tradeoff to debugging." Answer them; check answers against the file.
4. For each tool, also open the related source file in the repo and trace where it's used. The notes pair with code.
