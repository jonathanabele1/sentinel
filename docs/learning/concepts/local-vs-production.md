# Local development vs production

## What it is

A foundational distinction every working engineer eventually internalises: the environment you develop against on your laptop is *not* what runs in production, and that's by design. Different requirements, different trade-offs, different infrastructure.

When you see Sentinel running Postgres and Redis as containers in `docker-compose.yml`, that is the **local development setup**. It would be a mistake to deploy the same configuration to production at any company shipping at scale.

## What production actually looks like

In production at a serious company, the stateful services Sentinel uses would each be a **managed service** rented from a cloud provider.

| Service | Local dev | Production (typical) |
| --- | --- | --- |
| Postgres | Container from `postgres:16-alpine` image | AWS RDS for PostgreSQL, AWS Aurora Postgres, GCP Cloud SQL, Azure Database for PostgreSQL, Supabase, Neon |
| Redis | Container from `redis:7-alpine` image | AWS ElastiCache, GCP Memorystore, Upstash, Redis Cloud |
| Tracing backend (Jaeger) | Container running `jaegertracing/all-in-one` | Grafana Tempo (hosted), AWS X-Ray, Datadog APM, Honeycomb |
| Metrics backend (Prometheus) | Container running `prom/prometheus` | Grafana Cloud, AWS Managed Prometheus, Datadog, Chronosphere |
| Logs | structlog to stdout, viewable via `docker logs` | Centralised: Datadog Logs, Loki, CloudWatch Logs, Splunk |
| The application itself | Local Python process via `uv run uvicorn` | Container in Kubernetes / ECS / Fly / Cloud Run / Lambda |

## Why use managed services in production

You pay extra (often considerably extra) for a managed Postgres, and you get back:

- **Automated backups** with point-in-time recovery.
- **High availability**: failover replicas across availability zones; if a machine dies, traffic switches over in seconds.
- **Security patches** applied for you on a maintenance window.
- **Encryption at rest and in transit**, audit logs, network isolation (VPC).
- **Monitoring dashboards** out of the box.
- **24/7 oncall from someone else's team** if the database itself misbehaves.
- **Scaling**: bump the instance class with a few clicks instead of stopping the world.

Building and operating all of that yourself is a full-time team's worth of work. Renting it makes sense the moment revenue justifies the cost.

## Why use containers locally

If managed services are so good, why don't we use them locally too? Five reasons:

1. **Cost.** RDS is dollars-per-hour even at the smallest tier. Multiply by every dev on the team.
2. **Speed.** Local Postgres is ~0ms RTT. RDS over the public internet is 5-20ms per call. With dozens of calls per request, that adds up.
3. **Isolation.** Dropping a table mid-refactor is fine when it's your laptop's DB. The same act against shared infra is a Slack message you don't want to send.
4. **Offline work.** Plane, bad-wifi coffee shop, train tunnel.
5. **Reproducibility.** The Compose file pins `postgres:16-alpine`. Every contributor gets the bit-for-bit same database. Managed services drift between accounts (different versions, configs, extensions enabled).

## The bridge: the application doesn't know the difference

The crucial design point is that **your application code does not care** which kind of Postgres it's talking to. It reads a connection URL from the environment:

```python
# apps/api/config.py
class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
```

In dev that resolves to your local container:

```
postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel
```

In production it would resolve to RDS (or wherever):

```
postgresql+asyncpg://sentinel:hunter2@sentinel-prod.abc123.us-east-1.rds.amazonaws.com:5432/sentinel
```

Same code. Different `DATABASE_URL`. This is why **twelve-factor app** principles (https://12factor.net) put "store config in the environment" at the top of the list. The environment is the seam between dev and prod.

## What Sentinel's actual production deploy looks like

Week 7's deploy plan is honestly modest: a single small VPS (Hetzner ~€5/month, Fly.io free tier, Railway, DigitalOcean) running the same Docker Compose stack with Caddy fronting it for TLS. Postgres and Redis are co-located on the same box.

This is **not** how a real company would run Sentinel at scale. It is, however:

- Cheap enough to leave running for a portfolio piece.
- Real enough that the webhook flow, the TLS setup, and the production realities (log rotation, secret management, monitoring) are not faked.
- A clear demonstration that you understand the trade-off.

The plan also commits Kubernetes manifests under `infra/k8s/` *separately*, to show capability without paying EKS prices. The interview signal is "I know what production at scale looks like, I just chose not to pay for it for this portfolio piece."

## Where it leaks: things that *do* differ between dev and prod

A few things that aren't fungible across the dev/prod boundary, even with good config management:

- **Connection pooling.** Managed Postgres typically uses external pooling (PgBouncer, RDS Proxy). Local doesn't.
- **TLS to the database.** Production requires it; local doesn't bother.
- **Network egress.** Production calls to GitHub or Anthropic go through NAT gateways, fixed egress IPs, etc. Local goes directly out.
- **Performance characteristics.** Local Postgres on NVMe SSD outperforms RDS on networked storage in some cases and is dramatically slower in others (large parallel scans).
- **Resource limits.** Containers locally are unconstrained. Production has CPU/memory caps that surface different bugs.

These differences are why "integration tests against a real Postgres" matter (run them locally in CI against the same `postgres:16-alpine` image as dev) and why "test in staging that mirrors prod" matters even more.

## Why we use it this way

In one sentence: local dev optimises for *iteration speed* and *zero cost*; production optimises for *durability*, *availability*, and *security*. The application code is identical because the seam is in the environment.

## TL;DR

Production-grade Postgres at a real company is a managed service (RDS, Cloud SQL, etc.) with backups, failover, security patching, and an oncall team. Local dev uses a container because it's free, fast, offline, and isolated. Sentinel's app reads `DATABASE_URL` from the env, so the code is identical against either. Sentinel's Week 7 deploy uses a single VPS with containers (because it's a portfolio project, not a Series-B company); the K8s manifests committed alongside demonstrate that you know how to do the bigger thing.

## Interview-style questions

1. You're handed Sentinel and asked to make it production-ready for a real company. List five concrete changes you'd make starting from the current local Compose setup.
2. Why is the application code unchanged when moving from local Postgres to RDS? What design choice makes that possible?
3. Pick one managed Postgres service (RDS, Aurora, Supabase, Neon) and explain when you'd choose it over another. What trade-offs are you making?
4. Some teams run their entire production stack on Docker Compose on a single VPS. When is that *fine* and when does it stop being fine?
5. What does "twelve-factor app" mean in one sentence, and which factor is most relevant to the local-vs-prod boundary?
6. A bug shows up in production that doesn't reproduce locally. List four environmental differences between dev and prod that could explain it.
