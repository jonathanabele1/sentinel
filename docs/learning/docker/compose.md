# Docker Compose

## What it is

Docker on its own boots one container at a time via `docker run <image>`. That's fine for a single process, but a real application is usually several containers that depend on each other: a web service, a database, a cache, a message queue, a tracing backend. Managing five `docker run` commands plus a hand-created network plus environment variables plus shutdown ordering is miserable.

**Docker Compose is a tool for declaring a multi-container application in one YAML file and managing it as a single unit.** That's the entire pitch. The name comes from "composing" an application out of services, the same way you compose a function from smaller functions.

## What it does, vs plain Docker

| Without Compose | With Compose |
| --- | --- |
| One `docker run` per container, hand-typed each time | One `docker compose up` for everything |
| Manually create a network with `docker network create`, then `--network=…` on every run | Compose creates a private network and joins every service to it automatically |
| Service-to-service connectivity by IP or hand-managed hostnames | Service names *are* DNS names. `postgres` resolves to the Postgres container from inside any other container |
| Env vars threaded through each `-e KEY=VALUE` | An `environment:` block per service, or a shared `.env` file |
| Volumes hand-mounted with `-v` flags | Named volumes declared once at the bottom of the file, reused by any service |
| Startup order is your problem | `depends_on:` and healthchecks make Compose wait for things |
| Environment lives in your shell history | The whole environment is in a YAML file checked into git |

Mental model: Docker is the engine that runs one container; Compose is the *recipe interpreter* that says "to make this dish, boil these three pots in the same kitchen and let them talk."

## The Compose file format, walked through

The Sentinel compose file lives at `infra/docker-compose.yml`. Three top-level keys: `name`, `services`, `volumes`. Most files are exactly that shape.

```yaml
name: sentinel              # Compose "project name"; namespaces containers and the network.

services:                   # Each entry under here is one container we want running.
  postgres:                 # The service name. Also becomes the DNS hostname inside the network.
    image: postgres:16-alpine
    environment:            # Env vars passed into the container at boot.
      POSTGRES_USER: sentinel
      POSTGRES_PASSWORD: sentinel
      POSTGRES_DB: sentinel
    ports:                  # host_port:container_port. Exposes Postgres on your laptop's 5432.
      - "5432:5432"
    volumes:                # Named volume mounted at /var/lib/postgresql/data so data persists.
      - postgres_data:/var/lib/postgresql/data
    healthcheck:            # Compose runs this every interval to decide if the service is healthy.
      test: ["CMD-SHELL", "pg_isready -U sentinel -d sentinel"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:                    # Second service. Lives in the same private network as postgres.
    image: redis:7-alpine
    ports:
      - "6379:6379"
    ...

  jaeger:                   # Third service.
    image: jaegertracing/all-in-one:1.62
    ...

volumes:                    # Declares the named volumes. They survive `make down`.
  postgres_data:
  redis_data:
```

Three services, declaratively defined, ready to start as one unit. `docker compose up` reads this file, pulls images if missing, creates the network, attaches volumes, starts containers, runs healthchecks. `docker compose down` reverses it.

## Important concepts

### The Compose-created network

Every Compose project gets a private bridge network named `<project>_default`. All declared services join it. From inside a container, peer services are reachable by service name:

```
inside the redis container:
  $ ping postgres        # resolves to the postgres container's IP
  $ ping jaeger          # resolves to the jaeger container's IP
```

From your laptop you cannot use the service names directly. You reach the containers through the `ports:` mappings (e.g. `localhost:5432`).

### Volumes

A named volume is Docker's way of persisting data outside a container's lifetime. Without volumes, every `down`/`up` cycle wipes the database. With them, your data survives container recreation. Volumes live somewhere inside Docker's storage area, not in your project folder.

```bash
docker volume ls               # list all volumes
docker volume inspect sentinel_postgres_data   # see where it's stored
```

### Healthchecks

A healthcheck is a command Compose runs inside the container periodically to decide if the service is healthy. Postgres runs `pg_isready`; Redis runs `redis-cli ping`. The `(healthy)` annotation in `docker compose ps` is the result. Other containers can wait on these via `depends_on: condition: service_healthy`.

### The detached flag

`docker compose up -d` runs in **detached** mode: the containers go to the background, you get your shell prompt back. Without `-d`, Compose attaches your terminal to the containers' combined stdout, and Ctrl-C stops everything. For local development, detached is almost always what you want.

## Common commands

```bash
# Bring everything up in the background.
docker compose -f infra/docker-compose.yml up -d

# What's running?
docker compose -f infra/docker-compose.yml ps

# Tail logs for all services. Ctrl-C to detach (doesn't stop them).
docker compose -f infra/docker-compose.yml logs -f

# Tail logs for one service only.
docker compose -f infra/docker-compose.yml logs -f postgres

# Open a shell inside a running container.
docker compose -f infra/docker-compose.yml exec postgres psql -U sentinel
docker compose -f infra/docker-compose.yml exec redis redis-cli

# Stop and remove containers + network. Volumes survive.
docker compose -f infra/docker-compose.yml down

# Stop, remove containers + network, AND delete volumes. Wipes data.
docker compose -f infra/docker-compose.yml down -v
```

The Sentinel Makefile wraps the most common of these so you don't have to type the `-f infra/docker-compose.yml` every time. `make up`, `make down`, `make logs` are the shorthands.

## A historical note: `docker-compose` vs `docker compose`

You'll see this written two ways online:

- **`docker-compose`** (hyphenated) — the old standalone Python tool, installed separately. Mostly deprecated but still around on older machines.
- **`docker compose`** (with a space) — a plugin built into modern Docker Desktop, written in Go.

The YAML file syntax is the same. Use the space-separated form going forward.

## Where Compose fits in the ecosystem

```
Single container                    docker run
Multiple containers, one host       docker compose
Many containers, many hosts         Kubernetes
```

Compose is for *local development* and *small single-host deployments*. The moment you need scheduling across machines, rolling updates, self-healing, or autoscaling, that's Kubernetes territory. Sentinel uses Compose for local dev and commits Kubernetes manifests under `infra/k8s/` in Week 7 to demonstrate the bigger thing without paying cloud prices to host it.

## Why we use it

In one sentence: it turns "the working state of my local environment" from a thing that lives in shell history and tribal knowledge into a versioned file in the repo.

A new contributor clones Sentinel, runs `make up`, and gets the exact same Postgres version, the exact same Redis configuration, the exact same Jaeger setup as everyone else. CI uses the same file when it spins up integration tests. Production (in Week 7) uses the same file on the deploy VPS.

## TL;DR

Docker runs one container. Compose runs a *group* of containers as a declared application. The YAML file is the contract: services, the private network they share, the volumes they persist data to, and how they depend on each other. The Sentinel file declares three services (Postgres, Redis, Jaeger); `make up` is a one-line wrapper around `docker compose up -d` against that file.

## Interview-style questions to test yourself

1. What does Compose give you that `docker run` does not?
2. Inside a Compose network, how does service A reach service B? How does your laptop reach either?
3. What's the difference between `docker compose down` and `docker compose down -v`?
4. Why does the Postgres service in `infra/docker-compose.yml` declare a healthcheck? What can other services do with that information?
5. Why is `infra/docker-compose.yml` not in the repo root? (Trick question: it's a convention, not a requirement. The `-f` flag points Compose at it.)
6. When would you choose Kubernetes over Compose for a real deployment? What does Compose give up at that scale?
