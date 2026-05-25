# Sentinel

A production-grade GitHub PR review system with **deterministic orchestration**, **structured agent execution**, and a **rigorous evaluation harness**. Portfolio project targeting the SpaceX Platform Team role. Full plan lives in [docs/plan.md](docs/plan.md); job context in [docs/job_description.txt](docs/job_description.txt).

## Project intent

This is not a "GPT-in-a-webhook" PR bot. The whole point is to demonstrate reliability engineering applied to agentic AI. Every architectural decision should defend that thesis. If a change would blur the line between Sentinel and a typical LangChain demo, push back before implementing.

The five things that make it different and must never be compromised:

1. **Deterministic orchestration.** LLMs produce structured outputs; Python code drives control flow. The LLM never decides "what step runs next."
2. **Full audit trail + replayability.** Every step snapshots inputs/outputs to Postgres. Any past run can be replayed by ID.
3. **Rigorous evaluation.** Precision, recall, calibration (Brier score) measured against a labeled dataset. CI gates on regression.
4. **Production observability.** OpenTelemetry traces, Prometheus metrics, Grafana dashboards. Cost is a first-class metric alongside latency and errors.
5. **Reliability primitives.** Circuit breakers, exponential backoff with jitter, timeouts at every layer, graceful degradation, caching.

## Tech stack (locked)

These are not casual choices. Don't swap them out without an explicit conversation.

- **Python 3.12+**, FastAPI, Pydantic v2
- **PostgreSQL 16** with SQLAlchemy 2.0 async + Alembic
- **Redis 7** for cache/queue
- **Anthropic SDK directly.** No LangChain, no LlamaIndex. Frameworks hide what matters and senior reviewers see through them.
- **Docker + docker-compose** for local; **Kubernetes manifests** in `infra/k8s/` (deploy target is a single VPS, but k8s manifests exist to demonstrate capability)
- **OpenTelemetry** → Jaeger (local) / Tempo (prod)
- **Prometheus + Grafana** with dashboards committed as JSON in `infra/grafana/`
- **structlog** with JSON output
- **pytest + pytest-asyncio**, **ruff**, **mypy/pyright** (strict), pre-commit hooks
- **GitHub Apps API** (not OAuth, not PATs); HMAC-SHA256 webhook signature validation is mandatory

## Repository layout

```
sentinel/
├── apps/api/                    # FastAPI service (routes, deps, main)
├── packages/
│   ├── core/
│   │   ├── orchestrator/        # engine.py, step.py, plan.py, state.py
│   │   ├── agents/              # diff_analyzer, security/correctness/testing reviewers, consolidator
│   │   ├── llm/                 # client.py (Anthropic wrapper), retries.py, structured.py
│   │   ├── github/              # app.py, diff.py, comments.py
│   │   ├── models/              # domain.py (Pydantic), db.py (SQLAlchemy)
│   │   ├── observability/       # tracing.py, metrics.py, logging.py
│   │   ├── reliability/         # circuit_breaker.py, retry.py, budgets.py
│   │   └── cache/               # exact.py, semantic.py
│   └── eval/                    # dataset/, runner.py, metrics.py, judge.py
├── infra/                       # docker-compose, k8s/, grafana/
├── migrations/                  # Alembic
├── evals/                       # dataset.jsonl, reports/
├── docs/                        # architecture, design-decisions, runbook, eval-methodology
└── tests/
```

## Core domain model

The mental model the whole codebase revolves around. Implemented as Pydantic + SQLAlchemy in `packages/core/models/`.

- **ReviewRun** — one execution of the pipeline against a PR. Has cost, tokens, status, timestamps.
- **StepExecution** — one step within a run. Stores full input/output JSON for replay. This snapshotting is load-bearing; don't skip it.
- **DiffAnalysis** — structured output of the `diff_analyzer` agent.
- **ReviewFinding** — one issue identified by a reviewer agent. Has severity, confidence, evidence, `posted` flag.

## Build sequence (8 weeks, sequential)

Each week's Definition of Done is a hard prerequisite for the next. Don't jump ahead.

1. **Week 1 — Foundation.** Repo, FastAPI, docker-compose, Postgres+Alembic, GitHub App registration, webhook handler posting a placeholder comment.
2. **Week 2 — Orchestrator.** `Step`, `Plan`, `Engine` with per-step DB snapshotting, OTel spans, replay endpoint. Stub plan wired into webhook.
3. **Week 3 — Diff analyzer.** LLM client wrapper, structured outputs via Anthropic tool use, diff fetching, first real agent step.
4. **Week 4 — Specialist reviewers + consolidator.** Three parallel reviewers (security/correctness/testing). Deterministic consolidator (not an LLM). `.sentinel.yml` per-repo policy. Fan-out/fan-in in the orchestrator.
5. **Week 5 — Eval harness.** 50 labeled PRs, precision/recall/calibration, LLM-as-judge, CI regression gate, model comparison table.
6. **Week 6 — Reliability + observability + cost.** Full OTel/Prometheus, Grafana dashboards committed, circuit breaker, caching, chaos tests.
7. **Week 7 — Deploy + multi-repo.** Real VPS deploy, 5+ real repos using it, simple web UI (FastAPI + HTMX), feedback reactions tracked.
8. **Week 8 — Polish.** README, architecture doc, design-decisions doc, eval-methodology doc, demo video, blog post.

## Operating rules

### Architectural guardrails

- **The LLM never decides control flow.** It returns structured data. Python decides what runs next. If you find yourself wanting "an agent that picks the next agent," stop.
- **Tool use for structured outputs.** Force valid JSON via Anthropic tool use, validate against a Pydantic schema, retry on validation failure with the error as context. Never parse prose.
- **Specialists in parallel + deterministic consolidator.** Resist the urge to add an "agent manager agent" or have reviewers talk to each other.
- **Confidence > recall.** Better 90% precision at 60% recall than the reverse. Reviewers ignore noisy bots fast. Posting threshold lives in `.sentinel.yml`.
- **Snapshot inputs and outputs on every step.** This is what makes replay work. Full JSON in JSONB columns.
- **Correlation IDs end-to-end.** `run_id` and `pr_url` flow through logs, traces (as OTel baggage), and DB records.

### Code style

- Pydantic v2 for everything that crosses a boundary. SQLAlchemy 2.0 async for the DB.
- Type-check in strict mode. Public APIs in `packages/core/` should be fully typed.
- Tests as you go; target 70%+ on `packages/core/`. Eval set covers end-to-end; unit tests cover components.
- Don't reach for clever abstractions early. Three similar things is fine; abstract on the fourth.
- No comments explaining what the code does. Comments only for non-obvious why (subtle invariant, workaround for a known bug).
- No em dashes anywhere (user preference).

### Reliability defaults

- Timeouts at every layer: step-level, LLM-call-level, total-plan-level.
- Exponential backoff with jitter on every retry.
- Circuit breaker per provider.
- Graceful degradation: if security reviewer times out, post other findings with a note that security was skipped. Never fail the whole run because one specialist failed.
- Per-repo daily cost budget with soft alert at 80% and hard cap at 100%.

### Observability defaults

- Every external call (LLM, GitHub, DB, Redis) is its own span with attributes (model, tokens, cost, latency, retry count).
- Metrics named consistently: `llm_requests_total`, `llm_tokens_total`, `llm_cost_cents_total`, `llm_latency_seconds`, `step_duration_seconds`, `plan_duration_seconds`, etc.
- Dashboards in `infra/grafana/` as committed JSON; never click-configured-only.

## When making changes

- **New feature:** confirm which week it belongs to. If it's ahead of the current week, push back unless the user explicitly wants to skip ahead.
- **Touching the orchestrator:** preserve replay. Any new step must snapshot inputs/outputs and emit a span.
- **Touching prompts:** prompts have versions. Cache keys include the prompt version. Bump the version when changing.
- **Touching the eval:** preserve the regression gate (precision drop > 5% or recall drop > 10% fails CI). Statistical bounds, not raw thresholds.
- **Adding a dependency:** justify against the locked stack. Default answer is no.
- **Adding a new agent:** must be a `Step`, must have a Pydantic output schema, must be reachable through a `Plan`.

## Learning notes (docs/learning/)

When the user asks "what is X?" or "walk me through Y" and the answer is a generally-useful explanation of a concept, tool, or pattern (Docker Compose, ruff, OpenTelemetry, GitHub Apps, HMAC, deterministic orchestration, calibration, etc.), capture it as a standalone markdown file under `docs/learning/<topic>/<concept>.md` and link it from `docs/learning/README.md`.

These notes are for the user's later review (especially interview prep). They should be:

- **Self-contained.** A reader can pick up any single file cold.
- **Concrete.** Show code, commands, file contents from the actual Sentinel repo where possible.
- **Structured consistently.** What it is → what it does → why we use it → TL;DR → a few interview-style questions at the end.

Existing topics: `docker/compose.md`. Add to the index in `docs/learning/README.md` whenever a new file lands. Do NOT capture: ephemeral conversation, one-off debugging steps, things already covered in [architecture.md](docs/architecture.md) or this file.

## Anti-patterns to refuse

- LangChain, LlamaIndex, or any other framework that hides the LLM call.
- "Let the agent decide what to do next."
- Agents calling agents in a loop.
- Parsing free-form LLM prose instead of tool-use structured output.
- Posting every finding the model produces (no confidence threshold).
- Skipping the eval week to ship faster.
- Mock-only tests for code that hits Postgres, Redis, or the GitHub API.
- Click-configured Grafana dashboards that aren't in source control.
- Deploying to AWS for a $200/month bill when a €5 Hetzner VPS would do.

## Quick reference

```bash
make dev                    # docker-compose up, migrate, start API
make test                   # pytest with coverage
make lint                   # ruff + mypy
make eval                   # run eval harness against dataset
make replay RUN_ID=...      # replay a past run
make budget REPO=...        # show budget consumption for a repo
make chaos SCENARIO=...     # run a chaos test scenario
```

## Where to look

- Full week-by-week build plan with definitions of done: [docs/plan.md](docs/plan.md)
- Role context driving the project framing: [docs/job_description.txt](docs/job_description.txt)
- Architecture, design decisions, runbook, eval methodology: under `docs/` (to be authored during the build)

## Next project (don't think about it yet)

Sentinel feeds **Conduit**, a standalone AI gateway service in Go that the LLM client wrapper, cost tracking, retry logic, and provider abstraction will be extracted into. Sentinel then consumes Conduit. That's another 8 weeks. Finish Sentinel first.
