# Docker volumes

## What it is

A **volume** is Docker's mechanism for persisting data outside the lifetime of a container.

The thing to internalise: a Docker container's filesystem is **ephemeral**. When the container is removed, anything written inside it is gone. That works for a stateless web service that boots fresh every time. It does *not* work for a database that needs its tables to still exist after a restart.

A volume is the answer. It's a piece of storage that lives independently of any one container, and you mount it *into* containers at a specific path.

## How Sentinel uses volumes

Open `infra/docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

Two named volumes, declared at the bottom (`postgres_data`, `redis_data`), mounted into each container at the path that service writes its data to:

- `/var/lib/postgresql/data` is where Postgres stores its database files (the WAL, the table files, the system catalogs).
- `/data` is Redis's default snapshot/AOF directory.

When Postgres writes a row, it lands in the volume. When you `docker compose down`, the container is destroyed but the volume survives. When you `docker compose up`, Compose creates a fresh container and remounts the same volume back at the same path. Postgres reopens its data files. Your tables are still there.

## Lifecycle

```
docker compose up -d        # Containers created (or restarted). Volumes attached.
                            # If volume didn't exist yet, Docker creates it empty.

(your data writes happen)

docker compose down         # Containers removed. Network removed. Volumes UNTOUCHED.

docker compose up -d        # Fresh containers. Existing volumes remounted. Data is back.

docker compose down -v      # Containers removed. Network removed. Volumes DELETED.

docker compose up -d        # Fresh containers. Fresh empty volumes. Data is gone.
```

The `-v` flag on `down` is the only way Compose will delete volumes for you. Otherwise they persist indefinitely.

## Named volumes vs bind mounts

There are two main flavours, with very different use cases.

### Named volumes

```yaml
volumes:
  - postgres_data:/var/lib/postgresql/data
```

The left-hand side is a *name* (`postgres_data`). Docker manages everything: where on the host the data physically lives, the file permissions, the cleanup. You almost never interact with it as files on disk.

**Use for:** databases, caches, anything that needs to persist but where you don't care about the storage path.

**Inspect with:**

```bash
docker volume ls                           # list all named volumes
docker volume inspect sentinel_postgres_data   # see where it lives, what's mounted to it
```

### Bind mounts

```yaml
volumes:
  - ./infra/grafana/provisioning:/etc/grafana/provisioning:ro
```

The left-hand side is a *host path* (relative or absolute). Docker mounts that exact directory from your laptop into the container. Changes on the host show up immediately inside the container and vice versa.

**Use for:** config files you want to edit in your editor and have the container pick up; source code mounted for hot-reload during development; logs you want to ship to disk on the host.

The `:ro` suffix makes the mount read-only, which is good practice for config files because it prevents the container from accidentally writing back.

### Anonymous volumes

If you write `- /some/path/in/container` with no left-hand side, Docker creates an anonymous volume with a random ID. These tend to leak (you forget which one is which) and are mostly an antipattern. Stick to named or bind.

## Where do volumes actually live?

A common confusion: named volumes don't appear in your project folder. They live somewhere inside Docker's storage area.

On Docker Desktop for macOS, that's a Linux VM Docker manages on your behalf. The volumes live inside the VM at `/var/lib/docker/volumes/<volume-name>/_data`. You don't normally need to know this; `docker volume inspect` will tell you the exact path if you do.

You can think of named volumes as "named storage handles" rather than "directories on my Mac." The handle is what you reference; Docker handles where bytes actually go.

## What about production?

Docker volumes are mostly a local-development construct. In a real production deploy, you typically don't want your database storage to be a Docker-managed volume on the container host, for two reasons:

1. **The container host can die.** Volumes live on a specific machine; if the machine goes, the data goes. Real production wants storage on a separate failure domain.
2. **Backups, snapshots, monitoring** are the storage layer's job. Cloud block devices (AWS EBS, GCP Persistent Disk) and managed databases handle this; a Docker volume does not.

Real production patterns:

- **Managed databases (RDS, Aurora, Cloud SQL)** — you don't think about storage at all. It's the provider's problem.
- **Block storage attached to the VM** (EBS, Persistent Disk) — the database container mounts the block device path as a bind mount.
- **Kubernetes PersistentVolumes** — abstraction over cloud block storage; pods get `PersistentVolumeClaim`s that resolve to actual storage.

For Sentinel's Week 7 deploy on a single VPS, the named volumes from Compose are fine: it's one box, one stateful service, low stakes. For scale, you'd swap to RDS and drop the volume entirely.

## Common commands

```bash
# List every named volume on your machine.
docker volume ls

# Inspect one volume (size, mountpoint, which containers use it).
docker volume inspect sentinel_postgres_data

# Remove a single volume manually.
docker volume rm sentinel_postgres_data

# Remove every volume that isn't attached to a running container.
docker volume prune

# Wipe Sentinel's volumes specifically (data loss).
docker compose -f infra/docker-compose.yml down -v
```

## Why we use them

Without volumes, every `make down` would wipe your database. Every developer would re-run all migrations every morning. Every botched test would leave you in an unknown state. Volumes make the local development DB feel like a real database that survives sessions, while still being trivially resettable when you want it to be (one `-v` flag).

For Redis specifically: Redis is mostly in-memory, but if you ever enable RDB snapshots or AOF persistence, the volume gives Redis a place to write them. Even if you don't, mounting the volume costs nothing.

## TL;DR

Containers have ephemeral filesystems; whatever's written inside dies when the container is removed. A volume is a piece of storage that lives independently and gets mounted into containers at a specific path, letting data survive container restarts. Named volumes (`postgres_data`, `redis_data`) are managed by Docker; bind mounts (`./infra/grafana/provisioning:/etc/grafana/provisioning`) point at host directories. `docker compose down` leaves volumes alone; `docker compose down -v` deletes them. In production at scale you'd typically use managed databases or cloud block storage instead.

## Interview-style questions

1. What happens to your Postgres data when you run `docker compose down`? What happens with `down -v`? Why design it that way?
2. Walk through the difference between a named volume and a bind mount. Give one example of each from `infra/docker-compose.yml` and explain why that flavour is appropriate.
3. The `postgres_data` volume is mounted at `/var/lib/postgresql/data` inside the container. Why that specific path? What would happen if you mounted it at `/data` instead?
4. Could you replace the Compose named volumes with a bind mount to `./data/postgres` instead? What changes? What might go wrong?
5. In production, why is "store the database in a Docker volume on the container host" usually a bad idea?
6. You run `docker volume ls` and see twelve volumes you don't recognise. What's the safe way to find out which ones are unused and clean up?
