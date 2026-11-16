# python-ship

[![Quality](https://github.com/ship-ecosystem/python-ship/actions/workflows/quality.yml/badge.svg)](https://github.com/ship-ecosystem/python-ship/actions/workflows/quality.yml)
[![Docker integration](https://github.com/ship-ecosystem/python-ship/actions/workflows/docker-integration.yml/badge.svg)](https://github.com/ship-ecosystem/python-ship/actions/workflows/docker-integration.yml)
[![PyPI Version](https://img.shields.io/pypi/v/python-ship)](https://pypi.org/project/python-ship/)
[![Python Versions](https://img.shields.io/pypi/pyversions/python-ship)](https://pypi.org/project/python-ship/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/python-ship)](https://pypistats.org/packages/python-ship)
[![License](https://img.shields.io/pypi/l/python-ship)](https://github.com/ship-ecosystem/python-ship/blob/main/LICENSE.md)

A framework-agnostic, cross-platform Docker development environment for
Python web apps, with portable production releases. It is the Python member
of the Ship ecosystem, alongside [PHP Ship](https://github.com/php-ship/ship):
the same commands, the same `ship.json` vocabulary and the same release
artifact, for any ASGI app — a plain ASGI callable, Django or FastAPI built
in, anything else through a community adapter.

**Status: beta, first public release.** `init`/`up`/`down`/`exec`/`shell`/
`logs`/`db`/`config:test`/`build`/`release`/`npm`/`uv`/`manage` all work
end-to-end against a real Docker daemon, for a framework-free app, a Django
app and a FastAPI app alike: development stacks with a real database and
cache, production images, and releases booted on a machine with nothing but
Docker. CI runs the unit suite on Linux, macOS and Windows with Python
3.11–3.15, and the Docker suites on every push to `main`. See
[`docs/roadmap.md`](https://github.com/ship-ecosystem/python-ship/blob/main/docs/roadmap.md)
for what's built and what's next.

## Contents

- [Why not just write a Compose file?](#why-not-just-write-a-compose-file)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Services](#services)
  - [Silo on older CPUs](#silo-on-older-cpus)
- [Configuration (`ship.json`)](#configuration-shipjson)
  - [Adding a service later](#adding-a-service-later)
  - [Multiple instances of a service](#multiple-instances-of-a-service)
  - [Custom service names and an external network](#custom-service-names-and-an-external-network)
- [Dependencies and `ship uv`](#dependencies-and-ship-uv)
- [ASGI servers](#asgi-servers)
- [Production releases](#production-releases)
  - [Python security floor](#python-security-floor)
- [HTTPS / TLS](#https--tls)
- [Backing up data volumes](#backing-up-data-volumes)
- [Frontend dev server (Vite HMR)](#frontend-dev-server-vite-hmr)
- [File ownership in dev](#file-ownership-in-dev)
- [Debugging (debugpy)](#debugging-debugpy)
- [Faster file sync on Windows/macOS (Mutagen)](#faster-file-sync-on-windowsmacos-mutagen)
- [Running more than one project at once](#running-more-than-one-project-at-once)
- [Customizing the stack](#customizing-the-stack)
- [Extending ship](#extending-ship)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [License](#license)

## Why not just write a Compose file?

You can, and every project that uses Ship ends up with one — Ship generates
it. What Ship adds is everything around that file that each project would
otherwise rebuild by hand:

- **One cross-platform CLI.** `ship` is a Python executable, so `ship up`,
  `ship exec` and `ship db` run natively on Windows, macOS and Linux, with no
  shell scripts and no requirement to keep the project inside WSL.
- **A portable release artifact, not just a dev tool.** `ship release --tag`
  builds real production images (no bind mount, no uv or pip, a non-root
  user) from the same `ship.json` and packages them with a final Compose
  file, `.env.production`, and a deploy script into `dist/ship/<tag>/`. The
  server needs nothing but Docker — see
  [Production releases](#production-releases).
- **A real database shell without a published port.** `ship db` opens
  `psql`/`mysql` inside the database container, reading its credentials from
  that container's own environment.
- **Not tied to a framework.** The core knows nothing about Django or
  FastAPI; both are [`FrameworkAdapter`](https://github.com/ship-ecosystem/python-ship/blob/main/src/python_ship/contracts.py)
  implementations on the same public extension point a third party uses —
  the [Starlette example](https://github.com/ship-ecosystem/python-ship/tree/main/examples/community-adapter)
  is one. A plain ASGI callable needs no framework, `requirements.txt` or
  `pyproject.toml` at all.
- **Postgres and MySQL, Redis, four S3-compatible stores, Meilisearch,
  Mailpit, Selenium and Playwright, Node, Uvicorn and Hypercorn** — `ship
  init` picks one option per group; nothing is hardcoded to one choice.

## Requirements

- Python 3.11+ to run the `ship` CLI itself — or just
  [uv](https://docs.astral.sh/uv/getting-started/installation/), which
  installs `ship` with a Python it manages.
- Docker Desktop (or Docker Engine with Compose v2). This is what actually
  runs your project; `ship` drives `docker compose`.
- [uv](https://docs.astral.sh/uv/) on the host. `ship init` offers to install
  it with Astral's official installer if it's missing, and makes no changes to
  the project if you decline.
- An ASGI app. Nothing else: no framework, no dependency manifest, no local
  database, no Node install.

## Quick start

```sh
uv tool install python-ship   # or: pipx install python-ship
cd your-project
ship init                     # pick your services interactively
ship up                       # build and start
ship manage migrate           # Django only: registered when manage.py is detected
ship uv add httpx             # add a dependency; `ship up` rebuilds the image
ship shell                    # drop into the app container
ship down
```

`ship init` detects Django (`manage.py`) and FastAPI (a `FastAPI()` app) and
suggests the ASGI target to serve. A framework-free app works too — save this
as `main.py` and run `ship init`:

```python
async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"Hello from Ship"})
```

`ship init` writes `ship.json` and publishes a `ship/` directory (the
Dockerfile and its security check) into your project root — both are meant to
be committed. In scripts and CI, pass everything as options and add
`--no-interaction`:

```sh
ship init -n --framework django --app project.asgi:application --database postgres --cache redis
```

Production is a separate pair of commands, not a flag on `ship up` — see
[Production releases](#production-releases) for `ship build`/`ship release`.

## Commands

| Command | What it does |
|---|---|
| `ship init` | Interactive picker for the framework, ASGI target, server and services (database, cache, storage, ...); writes `ship.json` and publishes `ship/`. Every option has a flag for non-interactive use (`ship init --help`). Re-run it to change selections — see [Adding a service later](#adding-a-service-later). |
| `ship up` | Regenerates `ship/docker-compose.generated.yml` from `ship.json`, runs `docker compose up --build`, then waits until every container is running, every healthcheck passes and the app answers HTTP. Development only. |
| `ship build` | Builds every project-owned production image, tagged locally. Needs `.env.production` as soon as a selected service has a required credential. See [Production releases](#production-releases). |
| `ship release --tag <tag>` | Builds the same images (re-tagged) and assembles the portable release under `dist/ship/<tag>/`. |
| `ship down [--volumes]` | `docker compose down`. `--volumes` also deletes named volumes (database and storage data — use with care). |
| `ship exec <service> <cmd...>` | Runs a command inside a running service. The generic escape hatch every shortcut below wraps. |
| `ship shell [service]` | Opens a shell (`sh`) in a service; defaults to the app. |
| `ship logs [service] [-f]` | Shows logs for one service, or all of them. `-f`/`--follow` streams. |
| `ship db [instance]` | Opens the database's client (`psql`/`mysql`) with credentials from its own container. Name one of `additionalServices`' databases to open that instance instead. |
| `ship config:test` | Validates `ship.json`, `ship/`, both Compose models, and production credentials in `.env.production` — without Docker. Reports every problem it finds in one pass. |
| `ship doctor` | Shows the installed and published versions and the state and health of every container, with a recovery hint for anything that crashed. |
| `ship uv <args...>` | Runs uv dependency commands (`add`, `remove`, `lock`, `tree`, ...) in a temporary container with the project's Python. See [Dependencies](#dependencies-and-ship-uv). |
| `ship npm <args...>` | Runs `npm` in the Node frontend service (e.g. `ship npm install`, `ship npm run dev`). |
| `ship manage <args...>` | Runs `python manage.py` in the app — only registered for Django projects. A community adapter registers its own commands the same way. |
| `ship --version` | Shows the installed python-ship version. |

Every proxied command forwards its arguments exactly as typed (including
flags such as `ship manage migrate --database=other`), and returns the inner
command's exit code. `--project-directory PATH` before any command runs it
against another project. Without a terminal (CI, piped input), `exec`-style
commands pass `-T` to Compose, so `ship db < dump.sql` works.

## Services

`ship init` groups services (database, cache, storage, search, mail, testing,
frontend) and lets you pick one per group. "Wired" means the container runs,
is healthchecked where it can be, and the right variables are injected into
the app; it does not install the Python client library your app uses to
read them — that stays your app's decision.

| Service | Wired automatically | Still needed in the app |
|---|---|---|
| PostgreSQL (`postgres`) | `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`, `ship db` | A driver such as `psycopg[binary]`, and settings that read `DB_*` |
| MySQL (`mysql`) | The same `DB_*` variables, a separate `DB_ROOT_PASSWORD` the app never receives, `ship db` | A driver such as `mysqlclient` or `PyMySQL` |
| Redis (`redis`) | `REDIS_URL`; RDB snapshots plus an append-only log | `redis` |
| Mailpit (`mailpit`) | `MAIL_HOST`/`MAIL_PORT` (SMTP), web UI on `127.0.0.1:${MAILPIT_WEB_PORT:-8025}`. Development only | Nothing for `smtplib`; point your framework's mail settings at the variables |
| Meilisearch (`meilisearch`) | `MEILISEARCH_URL`/`MEILISEARCH_KEY` | `meilisearch` (or plain HTTP) |
| Garage / SeaweedFS / RustFS / Silo | `AWS_ENDPOINT`/`AWS_USE_PATH_STYLE_ENDPOINT`/`AWS_DEFAULT_REGION`/`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_BUCKET` | `boto3` with path-style addressing. Garage creates its bucket; with the others, create `AWS_BUCKET` once. Silo has two image variants — see [Silo on older CPUs](#silo-on-older-cpus) |
| Selenium (`selenium`) | A Chrome grid, `SELENIUM_REMOTE_URL`, `APP_URL`. Development only | `selenium` in your test dependencies |
| Playwright (`playwright`) | A browser server, `PLAYWRIGHT_WS_URL`, `APP_URL`. Development only | `playwright==1.64.1` — the client must match the server version |
| Node.js (`node`) | A Node container for `ship npm`, Vite's port published. Development only | See [Frontend dev server](#frontend-dev-server-vite-hmr) |

Connecting a Django app to the injected variables:

```python
import os

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "HOST": os.environ["DB_HOST"], "PORT": os.environ["DB_PORT"],
    "NAME": os.environ["DB_NAME"], "USER": os.environ["DB_USER"],
    "PASSWORD": os.environ["DB_PASSWORD"],
}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
                      "LOCATION": os.environ["REDIS_URL"]}}
```

A FastAPI or framework-free app passes the same values to its own clients:
`psycopg.connect(host=os.environ["DB_HOST"], ...)`,
`redis.from_url(os.environ["REDIS_URL"])`, or
`boto3.client("s3", endpoint_url=os.environ["AWS_ENDPOINT"], ...,
config=Config(s3={"addressing_style": "path"}))`. The
[Django](https://github.com/ship-ecosystem/python-ship/tree/main/tests/fixtures/django_app) and
[FastAPI](https://github.com/ship-ecosystem/python-ship/tree/main/tests/fixtures/fastapi_app)
fixtures the test suite runs are complete working examples.

Browser tests reach the app at `APP_URL` (`http://ship-app.test:8000`):
browsers force a single-label host such as `app` to HTTPS, so the app gets a
dotted alias whenever a testing service is selected.

```python
import os
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.connect(os.environ["PLAYWRIGHT_WS_URL"])
    page = browser.new_page()
    page.goto(os.environ["APP_URL"])
```

Every image carries an exact version tag, reviewed through Renovate:

| Service | Image |
|---|---|
| PostgreSQL | `postgres:17.12-alpine3.24` |
| MySQL | `mysql:9.7.4` |
| Redis | `redis:8.10.3-alpine3.23` |
| Mailpit | `axllent/mailpit:v1.31.1` |
| Meilisearch | `getmeili/meilisearch:v1.56.2` |
| Garage | `dxflrs/garage:v2.5.0` (built from `ship/garage/`) |
| SeaweedFS | `chrislusf/seaweedfs:4.46` |
| RustFS | `rustfs/rustfs:1.0.0` |
| Silo | `pgsty/silo:RELEASE.2026-09-16T00-00-00Z` |
| Selenium | `selenium/standalone-chrome:4.52.0-20261101` |
| Playwright | `mcr.microsoft.com/playwright:v1.64.1-noble` |
| Node.js | `node:24.18.0-alpine3.24` |

### Silo on older CPUs

Silo publishes two images of every release, and they don't run on the same
set of machines:

| `siloImage` | Image | What's in it |
|---|---|---|
| `standard` (default) | `pgsty/silo:<release>` | The server on a RHEL 9 base, with a shell and the usual tools. |
| `distroless` | `pgsty/silo:<release>-distroless` | The server binary only. No shell, so `ship shell silo` can't open one. |

RHEL 9 requires a CPU with the x86-64-v2 instruction set. On an older CPU —
or a virtual machine configured with a generic CPU model — the standard
image's container fails to start. `ship init` explains this and asks which
image to use whenever Silo is selected (`--silo-image` sets it
non-interactively), and records the answer in `ship.json`:

```json
"siloImage": "distroless"
```

Pick `distroless` if *any* machine that will run the stack might be affected.
To check a Linux machine, run
`/lib64/ld-linux-x86-64.so.2 --help | grep x86-64-v2`; `x86-64-v2 (supported,
searched)` means the standard image runs there. Both images are the same Silo
release on the same data volume, so you can switch later.

## Configuration (`ship.json`)

`ship init` writes this file; you rarely need to edit it by hand. Its field
names match PHP Ship's `ship.json`, so a team using both reads one format:

```json
{
    "framework": "django",
    "app": "project.asgi:application",
    "python": "3.13",
    "services": {
        "database": "postgres",
        "cache": "redis"
    },
    "additionalServices": [
        {"group": "database", "service": "mysql", "name": "analytics"}
    ],
    "extensions": []
}
```

- `framework` — the adapter: `generic` (any ASGI callable), `django`,
  `fastapi`, or a community adapter's key.
- `app` — the ASGI target to serve, `module:callable`. A module under `src/`
  works too.
- `python` — the Python branch built into the image (`ARG PYTHON_VERSION`).
  Keep it a bare branch (`"3.13"`) so every build gets that branch's latest
  patch release. See [Python security floor](#python-security-floor).
- `server` — `uvicorn` (default, omitted) or `hypercorn`, or a community
  server's key. See [ASGI servers](#asgi-servers).
- `services` — one selected service per group; a group without an entry
  means "none".
- `additionalServices` — extra, named instances (a second database, a second
  Redis, ...). See [Multiple instances of a service](#multiple-instances-of-a-service).
- `extensions` — entry point names of installed community adapters, services
  and servers to load. See [Extending ship](#extending-ship).
- `serviceNames` / `externalNetwork` — rename Compose services and attach the
  app to a pre-existing Docker network. See
  [Custom service names and an external network](#custom-service-names-and-an-external-network).
- `name` — the Compose project name; defaults to the directory name. See
  [Running more than one project at once](#running-more-than-one-project-at-once).
- `siloImage` — `"standard"` (default, omitted) or `"distroless"`. See
  [Silo on older CPUs](#silo-on-older-cpus).
- `publishPorts` — defaults to `false`, so production publishes nothing to
  the host. A reverse proxy on the project's Docker network reaches the app on
  port 8000. Set it to `true` only when you intend to expose
  `${APP_PORT:-8000}`; a Docker-published port bypasses most host firewalls.
  Production only — development always publishes what it needs, on
  `127.0.0.1`.
- `hostUser` — defaults to `true`. See [File ownership in dev](#file-ownership-in-dev).
- `deployCommands` — commands that run exactly once per deploy, **before**
  the app starts — migrations, typically:
  ```json
  "deployCommands": ["python manage.py migrate --noinput"]
  ```
  `ship release` writes them into the release's `deploy-commands.sh` rather
  than running them itself; that script starts the infrastructure services,
  waits for them to be healthy, runs each command in a one-off app container,
  and stops at the first failure. This is deliberately not where boot steps
  live: those run at every container start (see
  [Production releases](#production-releases)), which is exactly wrong for a
  migration — several containers would run it at once. Ship never runs
  migrations on its own.
- `processes` — extra long-running processes from the app's image: a worker,
  a scheduler. Name → command:
  ```json
  "processes": {
      "worker": "celery -A project worker --loglevel=info",
      "beat": "celery -A project beat"
  }
  ```
  Each becomes its own Compose service with the app's image, environment and
  networks, no published ports, and a 60s stop grace period so a worker can
  finish its job. They run in development too, against the same bind-mounted
  code. Write each as a shell command, `$VAR` included — Ship escapes every
  `$` so Compose doesn't blank out a variable meant for the container — or as
  an argument list (`["python", "worker.py"]`) to skip the shell.
- `nodeAssets` — build npm assets into the production image. See
  [Frontend dev server](#frontend-dev-server-vite-hmr).
- `djangoCollectstatic` — run `collectstatic` at every production boot
  (Django only). The files stay in the container, so serve them from the app
  (for example with WhiteNoise) or from a proxy that shares the volume.

Host-port collisions are handled with environment variables, not `ship.json`
— see [Running more than one project at once](#running-more-than-one-project-at-once).

### Adding a service later

Add the entry to `ship.json`'s `services` (e.g. `"cache": "redis"`) and run
`ship up` — it reads `ship.json` fresh every time. The exception is Garage,
which needs build files in `ship/garage/`; add that one by re-running
`ship init`.

Re-running `ship init` works for everything. Every prompt defaults to the
current selection, so pressing Enter keeps it; existing `additionalServices`
are kept, and every field it doesn't prompt for (`serviceNames`,
`externalNetwork`, `publishPorts`, `hostUser`, `processes`, `deployCommands`,
`name`, `extensions`, ...) carries over unchanged. It reads the old file
leniently, so one invalid field doesn't discard the rest.

Unlike PHP Ship, re-running `ship init` keeps your `ship/Dockerfile`: it is
yours to edit. `ship init --force` replaces it with the version matching the
installed python-ship — commit or back up your edits first. Ship records the
version that published the Dockerfile in `ship/.ship-version`, and `ship up`,
`ship init` and `ship config:test` warn when it differs from what's installed.

### Multiple instances of a service

`services` holds one selection per group. For a second database, a second
Redis kept apart from the default cache, or a second bucket store, add a
named instance:

```json
"additionalServices": [
    {"group": "database", "service": "postgres", "name": "analytics"}
]
```

`ship init` offers these interactively ("Add a named additional service
instance?") and validates the name. By hand, `name` must be lowercase
letters, digits and underscores, starting with a letter (`analytics`,
`queue_2`). It becomes two things:

- The variables' prefix: `ANALYTICS_DB_HOST`, `ANALYTICS_DB_PASSWORD`, ...
  instead of `DB_HOST`. Wire them into your settings like any second
  connection — for Django, a second `DATABASES` entry.
- The Compose service: `postgres-analytics`, with its own volume
  (`postgres-analytics_data`), and `ship db analytics` opens its shell.

Only `database`, `cache`, `storage`, `search` and `mail` support this; a
second browser driver or frontend toolchain isn't a coherent idea.

### Custom service names and an external network

Every Compose service has a fixed name by default: `app`, and each selected
service's key (`postgres`, `redis`, ...). Fine for one project on its own,
but projects that share a Docker network collide on those names. Hand-edit
`ship.json` when you need to:

```json
{
    "serviceNames": {"app": "client-app", "postgres": "client-db"},
    "externalNetwork": "shared_infra"
}
```

- `serviceNames` renames the app or a selected service. The hostname a
  service injects follows the rename (`DB_HOST=client-db`), as do `ship shell`
  with no argument, `ship db`, `ship manage` and Mutagen sync. Volumes keep
  their original names, so renaming never orphans data. Commands where you
  type the service (`ship exec`, `ship logs`, `ship shell <service>`) take the
  new name. Additional instances already have distinct names and aren't
  renamed.
- `externalNetwork` attaches the app (and every `processes` entry) to a
  Docker network created outside Ship, next to the project's own network —
  for reaching a database or cache that another Compose project runs. Point
  your `.env` at that project's hostnames.

## Dependencies and `ship uv`

The Dockerfile installs your dependencies with uv, in this order:

| Project has | Image runs |
|---|---|
| `uv.lock` + `pyproject.toml` | `uv sync --locked --no-dev` |
| `requirements.txt` | `uv pip install -r requirements.txt` |
| `pyproject.toml` only | `uv sync --no-dev` |
| none of them | nothing — only the ASGI server |

Then it installs the selected ASGI server, so a framework-free app with no
manifest still runs. Dependencies live in `/opt/ship-venv`, outside the
bind-mounted `/app`, so the mount can't hide them.

`ship uv add httpx`, `ship uv lock` and other dependency-metadata commands
run uv and the project's Python in a temporary container with the project
mounted: `pyproject.toml` and `uv.lock` change on the host, and neither host
Python nor a host `.venv` is involved. Run `ship up` afterwards to rebuild
the image. `ship uv sync`, `run`, `pip`, `python`, `tool` and `venv` are
refused, since their effects would vanish with the container — use `ship up`
to install and `ship exec app ...` to run.

## ASGI servers

Uvicorn is the default for every framework. Hypercorn is built in too:
`ship init --server hypercorn`, or `"server": "hypercorn"` in `ship.json`.
Ship installs the selected server into the image and runs it with reload in
development. The framework adapter only supplies the ASGI target, so any
server works with any framework, and a community package can add another
server (see [Extending ship](#extending-ship)).

In production, Uvicorn runs with `--proxy-headers` and reads two variables
from `.env.production`: `WEB_CONCURRENCY` (worker processes, default 1) and
`FORWARDED_ALLOW_IPS` (which proxies to trust for `X-Forwarded-*`; set it to
your proxy's address).

## Production releases

Production is two commands, not a flag on `ship up`:

```sh
ship build                  # build every project-owned production image
ship release --tag 1.2.0    # build + assemble the portable release artifact
```

Both build a materially different image from `ship up`'s development one:

- **No bind mount.** The code is copied into the image at build time, through
  the `builder` → `production` stages of `ship/Dockerfile`.
- **No build tools, not root.** The production stage carries the installed
  app and server only: no uv, no pip, and it runs as UID 10001.
- **Framework boot steps run at container *boot*, not build time.** Django
  gets `manage.py check --deploy` (and `collectstatic` with
  `djangoCollectstatic`) before the server starts, because those steps need
  the real environment, which only exists once the container starts.
  `FrameworkAdapter.release_commands()` is the extension point for this.
- **Fresh base images.** Production builds always pull the base image
  (`build.pull`), so a release never ships on a stale cached Python.
- **Development-only services disappear.** Mailpit, Selenium, Playwright and
  Node are absent from the production model, along with their variables.
- **No override file.** `docker-compose.override.yml` is a development
  convenience and never reaches a production image.

### Python security floor

Ship refuses to build on a Python release with known security
vulnerabilities. The current floor comes from the October 2026 expedited
security releases, which fixed the bundled pip certificate-chain problem,
tarfile link handling (CVE-2026-114583), and bundled Expat vulnerabilities
(CVE-2026-77214, CVE-2026-102633):

| Branch | Oldest release Ship builds on |
| --- | --- |
| 3.11 | 3.11.18 |
| 3.12 | 3.12.16 |
| 3.13 | 3.13.18 |
| 3.14 | 3.14.10 |
| 3.15 | 3.15.2 |
| 3.10 and older | none (end of life, no upstream fixes) |

It is enforced at three points:

- **`ship init`, `ship config:test` and every command that generates a
  Compose file** reject an end-of-life `python` branch, or a pinned patch
  release below the floor (`"3.13.4"`).
- **The Dockerfile's `base` stage** runs `ship/check-python-security-floor.py`
  against the Python actually in the image, and fails the build if it is below
  the floor. This catches a stale `python:3.13-slim` that Docker keeps reusing
  from its local cache; the fix is in the message: `docker pull` that image.
- **`ship build`/`ship release` always pull fresh base images.** `ship up`
  doesn't force a pull, so development still works offline.

The floor lives in `python_ship.security` and is raised whenever python.org
ships a security release.

### `.env.production`

A single project-level file, `.env.production`, is the one source of
production credentials and config — you create it; Ship never generates one.
`ship init` adds it to `.gitignore`. `ship build`/`ship release` make its
values available to `docker compose build`, and `ship release` copies the
whole file to the release's `.env`, read by the containers at boot.
`ship release` fails clearly if it doesn't exist.

Selected services' secrets (`DB_PASSWORD`, `DB_ROOT_PASSWORD`,
`MEILISEARCH_KEY`, `AWS_SECRET_ACCESS_KEY`, `GARAGE_RPC_SECRET`,
`GARAGE_ADMIN_TOKEN`, each with its instance prefix where one applies) are
*required* in production: Compose itself refuses to start without them, and
`ship config:test`/`ship build`/`ship release` check before Docker is invoked
— reporting a missing value, or one still set to a development default such
as `ship`. Non-secret settings (`DB_NAME`, `DB_USER`, `AWS_BUCKET`, ...) keep
their defaults unless you set them.

### What `ship release --tag` produces

```text
dist/ship/1.2.0/
├── docker-compose.yml   # final images: tags, no build:, no source needed
├── .env                 # copied from .env.production (mode 0600)
├── images/
│   ├── app.tar          # docker save — one per unique image, not per service
│   └── postgres.tar
├── release.json         # tag, git commit, python-ship version, image digests — no secrets
├── deploy-commands.sh   # only when deployCommands is set
└── README.md            # the exact commands to deploy this release
```

`dist/ship/` is git-ignored, and excluded from the Docker build context along
with `.env`/`.env.*`, `.npmrc` and `auth.json`, so no earlier release or
secret can end up in an image; `ship build`/`ship release` re-apply those
rules as the last lines of `.dockerignore` every time. On POSIX the release
directory is owner-only; on Windows, restrict it with filesystem ACLs before
sharing it. Services that build from identical definitions (the app and every
`processes` entry) share one image and one archive. Infrastructure images are
archived too, so the server never needs registry access.

A deploy, once the folder is on a machine with nothing but Docker:

```sh
docker load -i images/app.tar
docker load -i images/postgres.tar
sh deploy-commands.sh   # only present when deployCommands is set
docker compose up -d
```

Ship deliberately stops there — there's no `ship deploy` that SSHes anywhere.
Getting the folder onto the server is up to you; what Ship guarantees is that
the artifact needs nothing else once it's there.

### CI/CD

The same commands, the same artifact:

```yaml
- run: uv tool install python-ship
- run: ship release --tag ${{ github.ref_name }}
- run: # upload dist/ship/${{ github.ref_name }} however your pipeline deploys
```

`ship release` fails immediately without `--tag` when it isn't attached to a
terminal, instead of prompting and hanging the pipeline.

## HTTPS / TLS

The app listens on plain HTTP on port 8000; Ship doesn't terminate TLS. Put a
TLS-terminating reverse proxy in front — one you already run (a load
balancer, Cloudflare, Caddy, Traefik, nginx) or one you add through the
override file. A proxy on the host can use `publishPorts: true`; for example,
with `APP_PORT=8000` in `.env.production` and DNS pointing at the host, this
Caddyfile serves the release over HTTPS:

```caddyfile
example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Allow public traffic only to the proxy's ports, set `FORWARDED_ALLOW_IPS` so
the app trusts the proxy's forwarded scheme and client address, and configure
your framework's trusted hosts, secure cookies and HTTPS redirects. Serving
the bare published port to the internet means serving plain HTTP.

## Backing up data volumes

Every stateful service (PostgreSQL, MySQL, Redis, Meilisearch, the S3 stores)
keeps its data in a named Docker volume; `docker volume ls` lists them. A
release contains images and configuration, **not** that data. Ship doesn't
back volumes up; use your database's own tools, from the release directory:

```sh
docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > postgres.dump
docker compose exec -T postgres sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < postgres.dump

docker compose exec -T mysql sh -c 'exec mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' > mysql.sql
```

For Redis, Meilisearch and storage, use their snapshot procedures, or stop the
service and archive its volumes together (Garage has a data and a metadata
volume):

```sh
docker run --rm -v <project>_redis_data:/data -v "$PWD":/backup alpine tar czf /backup/redis-data.tar.gz -C /data .
```

Restore into fresh volumes with the same service version, and test a full
restore on a separate host. `ship down --volumes` deletes development volumes;
it is not a backup.

## Frontend dev server (Vite HMR)

Select `--frontend node`, then `ship npm install` and `ship npm run dev`. The
Node container publishes `${VITE_PORT:-5173}` on `127.0.0.1` — the same port
inside and outside — but Vite has to bind somewhere reachable from outside the
container and tell the browser where to connect back. Add this to
`vite.config.js` (`ship init` prints it whenever Vite is installed and the
config doesn't handle it yet):

```js
export default defineConfig({
    server: {
        host: '0.0.0.0',        // bind inside the container, not just loopback
        port: Number(process.env.VITE_PORT ?? 5173),
        strictPort: true,       // fail fast instead of silently picking an unpublished port
        hmr: { host: 'localhost' }, // what the browser connects back to
    },
});
```

Set `VITE_PORT` in `.env` to move both sides at once. Node isn't part of the
production stack. To compile assets into the production image, set
`nodeAssets` (`ship init --node-assets`), commit `package-lock.json`, and give
`package.json` a `build` script that writes to `dist/`: the Dockerfile builds
them in a separate Node stage and copies `dist/` into the Python image, so
Node never reaches the runtime.

## File ownership in dev

On Linux, the development app runs as your own UID and GID (`hostUser`,
default `true`), so files the container writes into the bind-mounted project —
migrations, collected static files, a `uv.lock` — stay yours. Set
`"hostUser": false` to use the image's default user instead. On macOS and
Windows, Docker Desktop maps bind-mount ownership itself, and under
`SHIP_MUTAGEN` there's no bind mount, so the setting does nothing there.
Production is unaffected either way: it always runs as UID 10001.

## Debugging (debugpy)

The development app can reach your IDE at `host.docker.internal` on every
platform. To attach a debugger to the app instead, add `debugpy` to your
development dependencies, publish its port with an override file, and start
the server under it:

```yaml
# docker-compose.override.yml
services:
  app:
    ports:
      - "127.0.0.1:5678:5678"
    command: ["python", "-m", "debugpy", "--listen", "0.0.0.0:5678",
              "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

Then attach your IDE to `localhost:5678`. Remove the override to go back to
the plain server.

## Faster file sync on Windows/macOS (Mutagen)

Docker Desktop's file-sharing layer makes bind mounts noticeably slower on
Windows and macOS than on Linux. [Mutagen](https://mutagen.io/) syncs the
project into the containers instead. It's opt-in and a per-machine choice —
pointless on Linux — so it's an environment variable, not a `ship.json` field:

```sh
SHIP_MUTAGEN=1 ship up
```

`ship up` creates a sync session for the app and each `processes` entry after
the stack starts, and waits for the first sync before reporting success;
`ship down` terminates them. Requires the [Mutagen CLI](https://mutagen.io/documentation/introduction/installation/)
on `PATH`. Virtual environments, `node_modules`, caches, build output, `.env`
files and VCS data are excluded — dependencies are installed into the image,
so run `ship up` again after changing them. Never applies in production.

## Running more than one project at once

Every generated Compose file carries a top-level `name:` (`ship.json`'s
`name`, or the sanitized directory name), which scopes networks and volumes,
and production uses `<name>-production`. Set `name` explicitly when it must
stay the same wherever the project is checked out.

Every published port has a variable, read from your shell or the project's
`.env`:

| Port | Variable | Default |
|---|---|---|
| App | `APP_PORT` | `8000` |
| Vite dev server | `VITE_PORT` | `5173` |
| Mailpit web UI | `MAILPIT_WEB_PORT` | `8025` |
| Silo console | `SILO_CONSOLE_PORT` | `9001` |

```sh
APP_PORT=8001 VITE_PORT=5174 ship up
```

Every one binds to `127.0.0.1` in development. Production publishes nothing
unless `publishPorts` is set, and then only the app on `${APP_PORT:-8000}`.

## Customizing the stack

`ship/docker-compose.generated.yml` is regenerated from `ship.json` by every
command, so editing it doesn't stick. Add a `docker-compose.override.yml`
(`compose.override.yaml` works too) at the project root instead — Compose's
own [merge rules](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)
apply, and every development command picks it up:

```yaml
# docker-compose.override.yml
services:
  adminer:
    image: adminer:5
    ports:
      - "127.0.0.1:8099:8080"
```

Key overrides by the *renamed* service when you use `serviceNames`. The
override never applies to `ship build`/`ship release`. For the image itself,
edit `ship/Dockerfile` directly — `ship init` keeps your edits unless you pass
`--force`.

## Extending ship

Third-party packages add services, framework adapters and ASGI servers
without forking Ship. A package registers a Python entry point:

```toml
[project.entry-points."python_ship.services"]
rabbitmq = "ship_rabbitmq:RabbitMQService"

[project.entry-points."python_ship.adapters"]
flask = "ship_flask:FlaskAdapter"

[project.entry-points."python_ship.servers"]
granian = "ship_granian:GranianServer"
```

Install it next to the `ship` command (`uv tool install python-ship --with
ship-rabbitmq`) and select it by entry point name:

```json
{
    "extensions": ["rabbitmq"],
    "services": {"queue": "rabbitmq"}
}
```

Nothing loads unless `extensions` names it. A missing, duplicate or broken
extension is a configuration error naming the extension. The contracts —
`ServiceDefinition`, `FrameworkAdapter`, `ASGIServer`, and the optional
`ProvidesDatabaseShell`/`ValidatesConfiguration` — are typed protocols in
[`python_ship.contracts`](https://github.com/ship-ecosystem/python-ship/blob/main/src/python_ship/contracts.py).
See [`docs/adding-a-service.md`](https://github.com/ship-ecosystem/python-ship/blob/main/docs/adding-a-service.md)
for a worked example, and the installable
[service](https://github.com/ship-ecosystem/python-ship/tree/main/examples/community-service) and
[adapter](https://github.com/ship-ecosystem/python-ship/tree/main/examples/community-adapter) examples.

## Architecture

- [`contracts.py`](https://github.com/ship-ecosystem/python-ship/blob/main/src/python_ship/contracts.py) —
  the public extension points and the `ServiceContext` each service receives.
- [`compose.py`](https://github.com/ship-ecosystem/python-ship/blob/main/src/python_ship/compose.py) —
  merges selected services' fragments into the development and production
  Compose models. Fragments merge rather than overwrite; a real conflict is
  an error, not a silent winner.
- [`config.py`](https://github.com/ship-ecosystem/python-ship/blob/main/src/python_ship/config.py) —
  reads and validates `ship.json`, reporting every problem at once.
- [`plugins.py`](https://github.com/ship-ecosystem/python-ship/blob/main/src/python_ship/plugins.py) —
  resolves `extensions` into adapters, services and servers.
- [`release.py`](https://github.com/ship-ecosystem/python-ship/blob/main/src/python_ship/release.py) —
  plans unique images and assembles the release artifact.
- [`templates/Dockerfile`](https://github.com/ship-ecosystem/python-ship/blob/main/src/python_ship/templates/Dockerfile) —
  multi-stage: `base` (with the security check), `builder`, `production` and
  `development`, plus an `assets` stage with `nodeAssets`.

## Contributing

See [`docs/adding-a-service.md`](https://github.com/ship-ecosystem/python-ship/blob/main/docs/adding-a-service.md)
for adding a service or adapter, and
[`docs/roadmap.md`](https://github.com/ship-ecosystem/python-ship/blob/main/docs/roadmap.md)
for what's built and what's planned.

```sh
python scripts/check_local.py   # Ruff, mypy, unit tests, then build and smoke-test both distributions
SHIP_DOCKER_TEST=1 python -m unittest tests.test_docker_development tests.test_docker_services
SHIP_PRODUCTION_DOCKER_TEST=1 python -m unittest tests.test_docker_production
SHIP_RELEASE_DOCKER_TEST=1 python -m unittest tests.test_docker_release
```

The Docker suites create and remove their own containers, volumes, networks
and images. Maintainers publish with [`docs/releasing.md`](https://github.com/ship-ecosystem/python-ship/blob/main/docs/releasing.md).

## License

MIT. See [`LICENSE.md`](https://github.com/ship-ecosystem/python-ship/blob/main/LICENSE.md).
