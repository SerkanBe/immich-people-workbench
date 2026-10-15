# Run with Docker

People Workbench runs in a separate container and connects to Immich through its
HTTP API. It does not need Immich's database or photo directories. Naming and
review stay local until you check Pending and explicitly choose Sync.

## Standalone Docker with remote Immich

Install Docker Engine with Compose, or Docker Desktop using Linux containers.
Download the source as described in [Getting started](getting-started.md), then
open a terminal in the folder containing `compose.yaml`:

```sh
docker compose up -d
```

The first start builds the image; later starts reuse the build cache.
No settings file is needed for local access. Open
[http://localhost:8766/](http://localhost:8766/) and enter your remote Immich
domain/IP in the connection form, for example `https://photos.example.com`.

### Change the port or browser URL

Copy `.env.example` to `.env` and edit the values you need. Compose reads `.env`
automatically. For example, to use port 8767:

```dotenv
PEOPLE_WORKBENCH_PORT=8767
```

Run `docker compose up -d` again, then open `http://localhost:8767/`.
For another hostname or HTTPS, also set `PEOPLE_WORKBENCH_PUBLIC_URL` to the
exact browser URL. Workbench stays bound to localhost unless you change
`PEOPLE_WORKBENCH_BIND_IP`.

Already using `.env.docker`? Rename it to `.env` if no `.env` exists, or keep
using `--env-file .env.docker`. Existing commands using `compose.build.yaml`
continue to work. Keep your existing project name (`-p` or
`COMPOSE_PROJECT_NAME`) so Workbench uses the same state volume.

## Add Workbench to Immich's Compose file

Use this setup when Immich and Workbench run on the same Docker host.
From the Workbench source folder, build its local image:

```sh
docker compose build
```

In your existing Immich Compose file, add the `people-workbench` entry under
`services` and `people-workbench-data` under `volumes`. Keep your existing
services and volume entries:

```yaml
services:
  people-workbench:
    image: people-workbench:local
    pull_policy: never
    init: true
    restart: unless-stopped
    ports:
      - "${PEOPLE_WORKBENCH_BIND_IP:-127.0.0.1}:${PEOPLE_WORKBENCH_PORT:-8766}:8766"
    environment:
      PEOPLE_WORKBENCH_PUBLIC_URL: ${PEOPLE_WORKBENCH_PUBLIC_URL:-http://localhost:${PEOPLE_WORKBENCH_PORT:-8766}}
      PEOPLE_WORKBENCH_TRUSTED_PROXIES: ${PEOPLE_WORKBENCH_TRUSTED_PROXIES:-}
    volumes:
      - people-workbench-data:/data
    read_only: true
    tmpfs:
      - /tmp:size=16m,mode=1777
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    stop_grace_period: 2m

volumes:
  people-workbench-data:
```

With the standard Compose network, both apps can now reach each other by
service name. If Immich uses custom networks, attach Workbench to the network
used by its server service.

From the Immich folder, start Workbench:

```sh
docker compose up -d people-workbench
```

Open `http://localhost:8766/` and enter `http://immich-server:2283` as the Immich
URL. Replace `immich-server` with your actual service name if different.
`localhost` still refers to Workbench's own container.

For later commands from the Immich folder, use `restart people-workbench`,
`logs --tail=50 people-workbench`, or `stop people-workbench` after
`docker compose`. To update, download the updated source, rebuild in the
Workbench folder, then repeat the start command from the Immich folder.

This setup creates a state volume in the Immich project. If moving existing
progress, read [Keep or move local progress](#keep-or-move-local-progress)
first. Do not run `docker compose down` against the Immich stack just to stop
Workbench.

## Connect to Immich

Use an Immich URL reachable from inside the container. `localhost` inside the
Workbench container means the container itself:

- Docker Desktop: use `http://host.docker.internal:2283` if Immich publishes
  port 2283 on the host.
- Shared Compose network: use the Immich service name and port, for example
  `http://immich-server:2283`.
- Separate Docker host: use Immich's reachable LAN address or hostname.

Enter this URL in Workbench's connection form. It is separate from
`PEOPLE_WORKBENCH_PUBLIC_URL`, which is the browser's Workbench address.
The container must trust Immich's HTTPS certificate.

<details>
<summary>Alternative: keep separate Compose projects on a shared network</summary>

### Share Immich's Docker network

If both apps use the same Docker host, Workbench can reach Immich directly
by service name. You can keep Workbench in its own Compose project.

Add this top-level block to Workbench's `compose.override.yaml`, keeping its
existing `services` section:

```yaml
networks:
  default:
    external: true
    name: immich_default
```

Replace `immich_default` with the network used by your Immich server.
`docker network ls` lists the available networks. The selected network must
already exist; Workbench will not create or remove it.

Run `docker compose up -d` from the Workbench folder. In its connection form,
enter `http://immich-server:2283`, replacing `immich-server` with your actual
Immich service name if different. This uses Immich's container port, even if
you publish it on a different host port. The browser still opens Workbench at
`http://localhost:8766/`; it does not need to resolve the Immich service name.

[Docker's networking guide](https://docs.docker.com/compose/how-tos/networking/#use-an-existing-external-network)
explains sharing networks between Compose projects.

Already connected? Keep the Immich URL bound to your existing progress.
Changing the hostname requires separate local state; see
[Keep or move local progress](#keep-or-move-local-progress).

</details>

## Manage standalone Docker

Run these commands from the same source folder:

```sh
docker compose restart
docker compose logs --tail=50
```

After downloading updated source, build and start the new version:

```sh
docker compose up -d --build
```

Stop Workbench while keeping its progress:

```sh
docker compose down
```

`down` keeps the named state volume. Do not add `--volumes` if you want to
retain progress. The container restarts without your login credentials; sign in
again. Startup and health checks never Sync Pending changes.

## Keep or move local progress

The named volume mounts at `/data` and stores names, drafts, review progress,
Pending, and reports. It is bound to the Immich URL and account ID. A new volume
starts with fresh state; Workbench does not import progress from Python or
another volume. Run only one Workbench container per state volume. Use the same
Immich URL and account when restoring a backup. A different server or account
needs a separate deployment and volume.

For backup or migration, stop Workbench and copy all of `/data`, including
SQLite WAL files and reports. Restore it while Workbench is stopped. The
container runs as UID/GID `10001:10001`; a host folder mount must be writable by
that user. Keep state on storage local to the Docker host, not an SMB/NFS share.
A backup preserves local requests; it cannot undo changes already applied to
Immich.

If Sync is interrupted or Workbench reports an uncertain merge, inspect Immich
and Pending before retrying. Do not assume a missing report means no Immich
change occurred.

<details>
<summary>Advanced setup: HTTPS reverse proxy</summary>

## Add HTTPS

For remote browser access, put Workbench behind an HTTPS reverse proxy and
restrict the hostname to your trusted LAN or VPN. The browser's Workbench URL
is the **public URL**; it is separate from the Immich URL entered on the
connection page. Set these deployment values in `.env`:

```dotenv
PEOPLE_WORKBENCH_PUBLIC_URL=https://people.example.net
PEOPLE_WORKBENCH_BIND_IP=127.0.0.1
PEOPLE_WORKBENCH_PORT=8766
PEOPLE_WORKBENCH_TRUSTED_PROXIES=192.0.2.10/32
```

Replace the example proxy address with the address Workbench sees. Configure
the proxy to preserve the original `Host` and set `X-Forwarded-Proto` to
`https`. Trust only the proxy's address or a restricted subnet; do not trust
`0.0.0.0/0` or `::/0`, or expose the backend port. URL subpaths are unsupported.
Keep the default local setup for localhost access.

</details>
