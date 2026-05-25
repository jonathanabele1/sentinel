# Why use Docker for local dev (and the alternatives)

## What this is about

A foundational question worth answering precisely: when Sentinel runs Postgres, Redis, and Jaeger as Docker containers locally, is Docker actually *required*, or is it one choice among many?

The honest answer is: **Docker is not required.** The application code never touches Docker. The Compose file is purely a developer-experience and reproducibility tool. But understanding *why* Docker is still the strong default matters more than the question itself.

## The mental model first

Sentinel's app reads a connection URL from an env var:

```python
DATABASE_URL=postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel
```

The app calls Postgres at `localhost:5432`. It does not know or care *how* Postgres got to be listening there. It could be:

- A container started by Docker Compose
- A container started by Podman, OrbStack, colima, or any OCI runtime
- A native Postgres process installed via Homebrew
- A tunnel forwarding `localhost:5432` to a cloud database somewhere

The "deployment of dependencies" is a separate problem from "the application code." Docker is one popular solution to the dependency-deployment problem.

## Five alternatives that work

### 1. Native install via Homebrew (or apt, dnf, etc.)

```bash
brew install postgresql@16 redis jaeger
brew services start postgresql@16
brew services start redis
jaeger --collector.otlp.enabled=true &
```

| Wins | Loses |
| --- | --- |
| No Docker Desktop overhead | Each dev has whatever version brew gave them |
| Native file I/O speed | Project A's Postgres 14 conflicts with project B's Postgres 16 |
| Familiar to most devs | State leaks across projects unless you're disciplined |
| | Different package names on macOS vs Linux |

### 2. Cloud dev databases (Neon, Supabase, Upstash)

```
DATABASE_URL=postgresql://...@ep-something.us-east-1.neon.tech/sentinel
```

Modern serverless Postgres lets each developer have an isolated branched database for free at small scale.

| Wins | Loses |
| --- | --- |
| Zero local setup | Requires internet |
| Real Postgres, not "Postgres on Mac" | 30-100ms latency per query (painful for tight loops) |
| Onboarding is one env var | Vendor lock-in for *dev*, which is silly |
| Free tiers exist | Costs money at scale |

### 3. In-process fakes (SQLite, fakeredis)

Swap Postgres for in-memory SQLite, swap Redis for the Python `fakeredis` library. Both expose the same Python API as the real thing.

| Wins | Loses |
| --- | --- |
| Tests run with zero setup | SQLite and Postgres are different databases |
| Bit-for-bit fast | JSONB doesn't exist in SQLite |
| | Migrations behave differently |
| | Tests pass locally, fail in production |

Acceptable for *some* unit tests. **Not acceptable** as Sentinel's full test strategy: the whole design leans on Postgres JSONB for replay.

### 4. Process supervisor (foreman, overmind, honcho, mprocs)

A `Procfile`:

```
postgres: /opt/homebrew/opt/postgresql@16/bin/postgres -D /tmp/sentinel-pg
redis:    redis-server --port 6379
jaeger:   jaeger --collector.otlp.enabled=true
api:      uv run uvicorn apps.api.main:app --reload
```

`overmind start` brings everything up, tails the combined logs.

| Wins | Loses |
| --- | --- |
| Light, no container overhead | Same version-pinning problem as Homebrew |
| Native speed | Each dev installs the binaries themselves |
| One command to start everything | Procfile is OS-specific (paths differ) |

### 5. Other container runtimes (Podman, OrbStack, colima, nerdctl)

All run the **same OCI images** as Docker. `postgres:16-alpine` works identically in every one of them.

- **OrbStack** is particularly popular on macOS: faster than Docker Desktop, lighter on memory, runs Compose files unchanged.
- **Podman** is the Red Hat-backed alternative, daemonless.
- **colima** is a minimal Lima-based runtime; popular among engineers who want only what they need.

These are alternatives to *Docker Desktop specifically*, not to containers as a concept.

| Wins | Loses |
| --- | --- |
| Same benefits as Docker | Less universal than Docker |
| Often lighter on resources | Some tooling assumes Docker by name |

## Why Docker wins as the default

So you have five real alternatives. Why default to Docker (Compose)?

A new contributor to Sentinel does this:

```bash
git clone …
brew install uv docker
make up
```

…and gets the bit-for-bit identical Postgres 16, Redis 7, and Jaeger 1.62 as everyone else. The versions are pinned in `infra/docker-compose.yml`. There is no question of "which Postgres did brew install for you last Tuesday."

Compare the Homebrew onboarding path:

```bash
brew install postgresql@16 redis jaeger
brew services start postgresql@16
# wait, brew uses /opt/homebrew/var/postgresql@16 but docs say /usr/local/var/postgres
# need to create the sentinel role and database
createuser sentinel
createdb sentinel -O sentinel
# also jaeger isn't a brew package, you need to download a tarball
# also I'm on M1 and one of the binaries is x86_64 only
# also there's a port conflict because another project's redis is already running
```

Every friction point is real and happens on every fresh machine.

The full comparison:

| | Native install | Docker Compose |
| --- | --- | --- |
| Version pinning | Whatever brew gave you | Exact, in the repo |
| Multiple Postgres versions side-by-side | Painful | Trivial (different project name per repo) |
| Easy reset | `dropdb && createdb` and hope you didn't miss state | `docker compose down -v` |
| Onboarding time | 30-60 min on a fresh laptop | 5 min |
| Same on Mac, Linux, Windows | Different paths, different services | Identical |
| Self-documenting | "ask Slack" | Read `infra/docker-compose.yml` |
| Production parity | Whatever brew gave you | Same image as production (sometimes) |
| Resource cost | Native, minimal | Docker Desktop is a few GB of RAM at idle |

The cost is that everyone needs a container runtime installed. In 2025, that's a non-cost.

## Two questions hiding in one

It pays to separate them in your head:

1. **"Do we need to containerise our dependencies?"** Mostly yes, for reproducibility and isolation. The alternative is each developer maintaining a slightly different version of every dependency on their machine, which is the "works on my machine" problem.
2. **"Do we need Docker specifically?"** No. Podman, OrbStack, colima are all fine; the same OCI image runs on each. Docker is the most universal name and Docker Desktop is the easiest install, so it's the default.

A senior interviewer would respect "we chose Compose for reproducibility and onboarding speed; the runtime choice is fungible." They would push back on "Docker because that's what people do."

## What Sentinel specifically loses if you removed Docker

Concrete scenarios. Imagine you ripped out `infra/docker-compose.yml` and switched to native installs:

- **Onboarding a contributor** goes from "5 minutes" to "afternoon-long Slack debugging session."
- **Version skew bugs** appear: developer A is on Postgres 14, the JSONB query that works for them silently breaks for developer B on Postgres 16.
- **"Wipe my data" is no longer a one-liner.** You go hunting for `/opt/homebrew/var/postgresql@16/` and pray you delete the right thing.
- **Jaeger** disappears unless you commit to a download-tarball-and-run script. Tracing locally becomes optional, which means it becomes neglected.
- **CI gets harder.** The CI workflow currently runs the same Compose file as local dev. Without it, CI needs a separate Postgres setup, which drifts from dev over time.

None of these are *fatal*. All of them are friction that compounds.

## TL;DR

Docker is not strictly needed. The app reads a connection URL and doesn't care how the service on the other end got there. You could use Homebrew, cloud dev databases, in-process fakes, a process supervisor, or a different container runtime. Docker (Compose) wins as the default because it gives you exact version pinning across every contributor, cross-OS consistency, trivial reset, and self-documenting setup in one file. The cost is that everyone needs a container runtime installed, which in 2025 is a non-cost. If you replaced Docker with OrbStack tomorrow, nothing about Sentinel would need to change.

## Interview-style questions

1. Walk through three concrete alternatives to running Postgres in a Docker container for local development. What does each give up?
2. Your team wants to drop Docker Desktop because it's eating memory. What's the smallest-impact migration path, and what stays the same in the codebase?
3. A junior engineer suggests using SQLite locally and Postgres in production "because they're both SQL databases." Make the counter-argument in two paragraphs.
4. Sentinel pins `postgres:16-alpine` in the Compose file. What goes wrong if you change it to `postgres:latest`?
5. You're running both Sentinel (which needs Postgres 16) and another project (which needs Postgres 14) on the same laptop. Walk through how the Docker approach handles this vs the Homebrew approach.
6. Distinguish "containers as a concept" from "Docker as a product." Which one is the architectural decision and which is the implementation detail?
