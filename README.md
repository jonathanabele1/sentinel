# Sentinel

Production-grade GitHub PR review system with deterministic orchestration, structured agent execution, and a rigorous evaluation harness.

## What this is

A platform-engineered alternative to the typical "GPT-wrapped-in-a-webhook" PR bot. The differentiators:

- **Deterministic orchestration.** LLMs produce structured outputs; Python code drives control flow.
- **Full audit trail.** Every step snapshots inputs and outputs to Postgres. Any past run is replayable.
- **Rigorous evaluation.** Precision, recall, and calibration measured against a labeled dataset. CI gates on regression.
- **Production observability.** OpenTelemetry traces, Prometheus metrics, Grafana dashboards. Cost is a first-class metric.
- **Reliability primitives.** Circuit breakers, exponential backoff with jitter, graceful degradation, caching.

## Getting set up (macOS)

Sentinel is developed on macOS. Linux works the same after substituting the package manager; Windows works via WSL2 (untested but should be fine).

### 1. Install Homebrew

If you don't have it: https://brew.sh

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Install the tools Sentinel needs

```bash
brew install uv                    # Python package manager + project runner
brew install --cask docker         # Docker Desktop (the GUI app + CLI)
```

Notes:

- `uv` is the modern Python package manager. It handles Python installation itself (via the `.python-version` file in this repo), so you don't need to install Python separately. More in [docs/learning/tooling/uv.md](docs/learning/tooling/uv.md).
- `docker` here installs **Docker Desktop**, the official Docker GUI app for macOS. It bundles the `docker` and `docker compose` CLIs with a graphical app that shows running containers, logs, and volumes. Alternatives: [OrbStack](https://orbstack.dev) or [colima](https://github.com/abiosoft/colima) (lighter, headless). More in [docs/learning/docker/why-docker.md](docs/learning/docker/why-docker.md).

### 3. Launch Docker Desktop

After install, open the Docker app from Applications (or Spotlight). The CLI will not work until the Docker daemon is running, and the daemon only starts when the app is running. You'll see a whale icon in your menu bar when it's up.

Verify:

```bash
uv --version
docker --version
docker ps                          # should return without error (empty list is fine)
```

### 4. Bring up Sentinel

```bash
cd path/to/sentinel
uv sync                            # install Python dependencies into .venv
cp .env.example .env               # fill in GITHUB_*, ANTHROPIC_API_KEY when ready
make up                            # start Postgres, Redis, Jaeger as Docker containers
make migrate                       # run Alembic migrations
make dev                           # start the FastAPI server on :8000
```

In another shell:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Open the Jaeger UI at http://localhost:16686 to see distributed traces once the system starts emitting them.

## Daily commands

```bash
make help         # show all available targets
make check        # lint + typecheck + unit tests (run before every push)
make format       # ruff auto-fix everything
make test         # full pytest with coverage
make up           # start Docker containers
make down         # stop them (data survives via volumes)
make logs         # tail container logs
make dev          # full local stack (up + migrate + API)
```

## Repository layout

See [CLAUDE.md](CLAUDE.md) for the full mental model and operating rules. The short version:

```
apps/api/                 FastAPI service (webhook, health, admin)
packages/core/            Orchestrator, agents, LLM client, GitHub client, models, observability
packages/eval/            Evaluation harness
infra/                    docker-compose, k8s manifests, Grafana dashboards
migrations/               Alembic migrations
docs/                     architecture.md, design-decisions.md, runbook.md, eval-methodology.md
```

## Documentation

- [Architecture](docs/architecture.md)
- [CLAUDE.md](CLAUDE.md) — operating guide for AI-assisted development
