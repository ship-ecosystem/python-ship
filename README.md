# Python Ship

An early scaffold for a framework-agnostic Docker development environment for
Python web apps, inspired by [PHP Ship](https://github.com/php-ship/ship).
This is a separate Python package and repository. Plain ASGI, Django, and
FastAPI apps use the same public adapter contract; infrastructure services use
a separate contract.

## Install

The standalone uv executable does not require a preinstalled Python. From
this checkout, uv can install the Ship CLI with an isolated Python that it
manages:

```sh
uv tool install --python 3.13 .
```

For package development with an existing Python 3.11+ environment, you can
also use:

```sh
python -m pip install -e .
```

The distribution name is provisionally `python-ship`, the import name is
`python_ship`, and the installed command is `ship`. The intended repository is
`github.com/python-ship/ship`. PyPI project ownership has not yet been claimed.

The `ship` entry point is one cross-platform Python CLI, like PHP Ship's PHP
CLI. The uv tool installation above obtains an isolated Python for that CLI;
the application and its dependency commands still run in Docker. Like PHP
Ship's requirement for host PHP, the CLI has a host language runtime; uv
manages that runtime without requiring a preinstalled system Python.

`ship init` requires [uv](https://docs.astral.sh/uv/getting-started/installation/).
If uv is missing, Ship asks whether to install it with Astral's official
installer. Answering no, or an installer failure, stops initialization before
any project files are written. The generated Dockerfile also uses uv to install
the app and Ship's default Uvicorn server inside the container. When a project
has `uv.lock`, the image runs `uv sync --locked --no-dev`.

## Start a project

A plain Python ASGI app needs no web framework, `requirements.txt`, or
`pyproject.toml`. For example, save this as `main.py`:

```python
async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"Hello from Ship"})
```

Then run:

```sh
ship init --app main:app
ship config:test
ship up
```

The default adapter is `generic`; Ship installs Uvicorn in the container and
uses it to serve the app. The app can declare its own dependencies separately
when needed.

In an existing Django project with `manage.py` and `project/asgi.py`:

```sh
ship init --framework django --app project.asgi:application --database postgres --cache redis
ship config:test
ship up
ship manage migrate
ship db
ship down
```

The Django adapter serves the ASGI target with Uvicorn in development.
`ship manage` passes arguments to `python manage.py` inside the app container.
Ship does not run migrations when the container starts.

In an existing FastAPI project with `app/main.py`:

```sh
ship init --framework fastapi --app app.main:app --database postgres
ship up
ship exec app python -m alembic upgrade head
```

`ship init` writes `ship.json` and `ship/Dockerfile`. `ship up` generates
`ship/compose.generated.json`, then invokes Docker Compose. Compose accepts
JSON files. Project code is bind mounted for development, and port 8000 binds
to localhost by default. Set `SHIP_APP_PORT` in the project `.env` to choose a
different localhost port, especially when running two Ship projects at once.
On macOS or Windows, install the [Mutagen CLI](https://mutagen.io/documentation/introduction/installation/)
and set `SHIP_MUTAGEN=1` in the host shell when running `ship up` to synchronize
source through Mutagen instead of a Docker bind mount. Ship waits for the first
sync into the app and any named process containers before reporting startup;
`ship down` terminates that project's sync
session. The app image installs Python dependencies during build, before sync;
local virtual environments, caches, build output, `.env` files, and VCS
directories are excluded from synchronization.
Compose scopes networks and named volumes by project name; set a distinct
`name` in each `ship.json` if the directory names would collide. A project
`.env` is loaded into the app container when present.
`compose.override.yaml` is applied when present. Use
`ship logs`, `ship shell`, `ship exec`, and `ship down --volumes` as needed.
Use `ship --project-directory /path/to/project COMMAND` to run any development
command from another working directory; the override is resolved from that
project root for `up`, `down`, `logs`, `shell`, `exec`, `manage`, `db`, and `uv`.
`ship up` waits for the expected containers, their healthchecks, and a reachable
app port before reporting success. If startup fails, inspect `ship logs`.
Run `ship doctor` to see the Ship, Python, server, and service image versions,
published Dockerfile warning, and each container's state and health. It reports
missing, exited, restarting, or unhealthy services with a recovery command.

## Production builds

Run `ship config:test` to validate the development and production models,
including required credentials in `.env.production`. Run `ship build` to write
`ship/compose.production.json` and build the production app image from the
same `ship.json` used for development. This model builds the `production`
Dockerfile target, reads `.env.production`, omits the source bind mount, and
does not publish the app port. It has a separate Compose project name from
development. The image runs as UID 10001 and contains the
installed app and ASGI server without uv or pip. Run `ship init --force` in an
existing project to publish the new multi-stage Dockerfile before using this
command. Set `publish_ports` to `true` in `ship.json` to expose port 8000 in
the production model; `SHIP_PRODUCTION_PORT` changes the host port. Framework
adapters keep their production startup behavior separate: Django runs
`manage.py check --deploy` before the ASGI server, while generic ASGI and
FastAPI start their selected server directly. Set `django_collectstatic` to
`true` to run `collectstatic --noinput` on Django startup after configuring
`STATIC_ROOT`; the output stays in the container unless the app serves it or
an external webserver consumes it.

For a long-running worker or scheduler, set `processes` in `ship.json` to a
mapping of Compose service names to argument lists, for example
`{"worker": ["python", "worker.py"]}`. Each process uses the application
image and environment without publishing a port. To compile npm assets into
the production image, run `ship init --node-assets` and provide a
`package-lock.json` plus a `build` script that writes to `dist/`. Node and
`node_modules` stay out of the Python runtime image. Existing projects can
set `node_assets` in `ship.json` and run `ship init --force` to refresh the
Dockerfile.

Running `ship init` again preserves the existing selections, extensions, and
Dockerfile. Ship records the version of its published Dockerfile and warns when
it differs from the installed package. Use `ship init --force` to refresh that
Dockerfile. `ship config:test` collects independent configuration errors without
starting Docker. The CLI uses Typer, including shell completion. Arguments after
`ship exec SERVICE` and `ship manage` pass through to the container command.

The application must read the injected `DB_*` or `REDIS_URL` variables in its
own settings. Django and FastAPI projects declare those framework libraries in
their own dependencies. Ship supplies Uvicorn by default; the Dockerfile uses `uv.lock`
when present, otherwise installs `requirements.txt` or the project from
`pyproject.toml` using uv. A plain ASGI app can have none of these files.

Use `ship uv add PACKAGE`, `ship uv lock`, or other dependency metadata commands
to run uv and the selected Python version in a temporary Docker container.
Ship bind mounts the source project so `pyproject.toml` and `uv.lock` edits
remain on the host, while the temporary Python environment stays inside the
container. Dependency resolution uses neither host Python nor host uv.
`ship uv sync`, `run`, `pip`, `python`, and `tool` are rejected because their
environment changes would be discarded with the temporary container. Run
`ship up` to rebuild the app image with changed dependencies. A locked uv project uses
`uv sync --locked --no-dev`; an unlocked `pyproject.toml` uses
`uv sync --no-dev`; `requirements.txt` uses `uv pip install -r`. An installable
project is installed by uv, while a project without dependencies can omit all
three files. If you use `uv` directly inside the app container, an exact
`uv sync` can remove Ship's server; `ship up` restores the image.

On Linux, the development app container runs with the host user's numeric
UID and GID so files created in the bind mounted project remain editable on
the host. On macOS and Windows, Docker Desktop handles bind mount ownership;
Ship leaves the container's default user in place. Production user policy is
separate from this development setting.

### ASGI server choice

Uvicorn is the default for plain ASGI, Django, and FastAPI apps. Select
Hypercorn with `ship init --app main:app --server hypercorn`, or set
`"server": "hypercorn"` in `ship.json`. Ship installs the selected server in
the app image and uses its development reload command. The framework adapter
still validates the app and provides framework commands; server choice is
independent. If a project has an older `ship/Dockerfile`, run
`ship init --force --server hypercorn` to publish the version that accepts
`SHIP_SERVER_REQUIREMENT`.

### Application settings for PostgreSQL and Redis

Selecting `--database postgres --cache redis` adds healthy PostgreSQL and Redis
containers and injects `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`,
and `REDIS_URL` into the app container. Ship does not install database or Redis
Python clients into the app. Declare them in your project's dependencies, such
as `psycopg[binary]` and `redis`.

For Django, map the variables in `settings.py`:

```python
import os

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "HOST": os.environ["DB_HOST"],
    "PORT": os.environ["DB_PORT"],
    "NAME": os.environ["DB_NAME"],
    "USER": os.environ["DB_USER"],
    "PASSWORD": os.environ["DB_PASSWORD"],
}}
REDIS_URL = os.environ["REDIS_URL"]
```

For FastAPI or a framework-free app, pass the same `DB_*` values to your
database client and use `redis.from_url(os.environ["REDIS_URL"])` for Redis.
The [Django](tests/fixtures/django_app) and [FastAPI](tests/fixtures/fastapi_app)
fixtures contain working examples and HTTP checks for both connections.

### Built-in development services

Select services with `ship init --database mysql --cache redis --mail mailpit
--search meilisearch --testing playwright --frontend node`, or select only the
ones the app uses. All are optional. MySQL injects the same `DB_*` variables
as PostgreSQL. Its application password is `DB_PASSWORD`; its separate root
password is `DB_ROOT_PASSWORD`. `DB_USER=root` is rejected during configuration
validation. `ship db` opens the selected database's client with credentials
from its container. PostgreSQL, MySQL, Redis, and Meilisearch use named volumes.
Redis saves snapshots and an append-only log.

Mailpit injects `MAIL_HOST` and `MAIL_PORT` for SMTP, and publishes its web UI
at `http://127.0.0.1:8025`. Set `MAILPIT_WEB_PORT` in `.env` if that host port
is occupied. Meilisearch injects `MEILISEARCH_URL` and `MEILISEARCH_KEY`;
set `MEILISEARCH_KEY` to a real secret for production. Selenium injects
`SELENIUM_REMOTE_URL` and `APP_URL` for browser tests. Install the `selenium`
Python library in the app if tests use its client:

```python
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

options = Options()
options.add_argument("--headless=new")
driver = webdriver.Remote(
    command_executor=os.environ["SELENIUM_REMOTE_URL"], options=options
)
driver.get(os.environ["APP_URL"])
driver.quit()
```

Alternatively, select
`--testing playwright` to run a Playwright browser server. It injects
`PLAYWRIGHT_WS_URL=ws://playwright:3000/` and `APP_URL=http://ship-app.test:8000` into
the app container. Declare `playwright==1.64.1` in the app's test dependencies;
the Python client and remote server must use matching Playwright versions.
The `.test` app address allows browsers to reach Ship's HTTP development server.
For example:

```python
import os
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.connect(os.environ["PLAYWRIGHT_WS_URL"])
    page = browser.new_page()
    page.goto(os.environ["APP_URL"])
    browser.close()
```

The browser server uses the official Playwright image and downloads the pinned
server package on its first start, so that first start needs network access.
Mailpit, Selenium, and Playwright are development services and are omitted
from the production Compose model.

Node is optional and keeps its dependencies in a named `node_modules` volume.
Run `ship npm install` and `ship npm run dev -- --host 0.0.0.0` for a project
with a frontend script. Port 5173 is published on localhost; set
`SHIP_HMR_PORT` in `.env` to change the host port. Node is omitted from the
production Compose model.

The built-in images are version tagged so upgrades can be reviewed:

| Service | Image tag |
| --- | --- |
| PostgreSQL | `postgres:17.12-alpine3.24` |
| MySQL | `mysql:9.7.4` |
| Redis | `redis:8.10.3-alpine3.23` |
| Mailpit | `axllent/mailpit:v1.31.1` |
| Meilisearch | `getmeili/meilisearch:v1.56.2` |
| Selenium Chrome | `selenium/standalone-chrome:4.52.0-20261101` |
| Playwright browsers | `mcr.microsoft.com/playwright:v1.64.1-noble` |
| Node | `node:24.18.0-alpine3.24` |

### S3-compatible storage

Choose one with `ship init --storage garage`, `--storage seaweedfs`,
`--storage rustfs`, or `--storage silo`. None is required by a plain Python
app. All four inject `AWS_ENDPOINT`, `AWS_USE_PATH_STYLE_ENDPOINT`,
`AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and
`AWS_BUCKET`. The app supplies an S3 client such as `boto3` in its own
dependencies. For example:

```python
import os
import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["AWS_ENDPOINT"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ["AWS_DEFAULT_REGION"],
    config=Config(s3={"addressing_style": "path"}),
)
```

Garage provisions its default `local` bucket and credentials at startup. Its
`ship/garage/Dockerfile` and `garage.toml` are published by `ship init`, and
each instance builds with its own RPC address. SeaweedFS generates an S3
identity file from the selected credentials before starting; without that
file its S3 gateway would be unauthenticated. RustFS and Silo require the app
to create `AWS_BUCKET` once with an S3 client before first use. Stateful data
lives in named Docker volumes; Garage also has a separate metadata volume.

Set `AWS_SECRET_ACCESS_KEY` in production, plus `GARAGE_RPC_SECRET` and
`GARAGE_ADMIN_TOKEN` for Garage. Development defaults are for local use.
Silo publishes its console on `http://127.0.0.1:9001` in development; set
`SILO_CONSOLE_PORT` in `.env` to change the host port. The default
`silo_image` is `distroless`. Select the standard variant with
`ship init --storage silo --silo-image standard` or set
`"silo_image": "standard"` in `ship.json`. The standard image requires an
x86-64-v2 CPU on x86 hosts; the distroless variant avoids that requirement.
Named Silo instances do not publish a console port by default, avoiding host
port collisions.

| Storage | Image or build base | S3 port | Bucket at startup |
| --- | --- | ---: | --- |
| Garage | `dxflrs/garage:v2.4.1` | 3900 | Created |
| SeaweedFS | `chrislusf/seaweedfs:4.46` | 8333 | Create once |
| RustFS | `rustfs/rustfs:1.0.0` | 9000 | Create once |
| Silo | `pgsty/silo:RELEASE.2026-09-16T00-00-00Z-distroless` | 9000 | Create once |

### Additional service instances and external networks

The `services` object keeps its simple one-per-group form. Add more instances
with `instances`, each with a unique instance ID, service group, service key,
and optionally a Compose name, environment prefix, or external network alias:

```json
{
  "framework": "generic",
  "app": "main:app",
  "services": {"database": "postgres", "cache": "redis"},
  "instances": {
    "analytics": {"group": "database", "service": "postgres", "name": "analytics-db"},
    "queue": {"group": "cache", "service": "redis", "name": "queue-redis", "network": "shared"}
  },
  "networks": {"shared": "existing-docker-network"}
}
```

Create external networks separately before `ship up`. Ship keeps its own
project network and attaches the app and selected instance to the requested
external network. The examples above add `ANALYTICS_DB_HOST`,
`ANALYTICS_DB_PORT`, `ANALYTICS_DB_NAME`, `ANALYTICS_DB_USER`,
`ANALYTICS_DB_PASSWORD`, and `QUEUE_REDIS_URL` to the app. Extra instances use
separate named volumes. Set `prefix` on an instance to choose a different
uppercase variable prefix. Run `ship db --service analytics` to open that
database's shell. The database client uses credentials set inside its container.

Compose service names and environment prefixes must be unique. Development
ports, including ports from extensions without an explicit host address, bind
to `127.0.0.1` by default. Ship uses the validated `name` field as
the Compose project name, or derives a stable name from the project directory.

## Extensions

An installed package can register an adapter or a service in its
`pyproject.toml`:

```toml
[project.entry-points."python_ship.adapters"]
flask = "ship_flask:FlaskAdapter"

[project.entry-points."python_ship.services"]
mailhog = "ship_mailhog:MailHogService"

[project.entry-points."python_ship.servers"]
community = "ship_community:CommunityServer"
```

The project then opts into each plugin by entry point name in `ship.json`:

```json
{
  "framework": "flask",
  "app": "app:app",
  "services": {"mail": "mailhog"},
  "extensions": ["flask", "mailhog"]
}
```

See [the public contracts](src/python_ship/contracts.py). Plugins must already
be installed in the environment where the `ship` CLI runs. An unknown or
duplicate extension is reported as a configuration error. Framework adapters
provide validation, development and production commands, console commands, and
declarative `release_commands`. The Django adapter declares a migration command
for a future release workflow; Ship does not execute release commands during
`ship up` or on container boot. Community adapters implement the same contract.
Service definitions may also provide `compose_fragment_for(config, instance)`
and `app_environment_for(config, instance)` to use the selected instance's
name, prefix, and network. A database service may provide
`database_shell(config, instance)` for `ship db`. Set `development_only = True`
on a service to omit it and its app variables from the production Compose model.
Ship merges app fragment maps and lists with its base service and reports scalar
conflicts instead of silently replacing existing settings.

A community ASGI server supplies `key`, one PEP 508 `requirement`, and
`development_command(target)` / `production_command(target)` methods that
return Compose command lists. Select its entry point in `extensions` and its
key in `server`. A framework adapter can opt into server selection by providing
`server_target(config)`; the built-in adapters do this. An adapter without that
hook keeps its own commands when Uvicorn is selected.

## Current scope

This scaffold implements development Compose generation, lifecycle commands,
generic/Django/FastAPI adapters, a growing built-in service catalog, Uvicorn or
Hypercorn, and plugin loading. Production builds, portable releases, and broader
dependency manager support are next work.
The Dockerfile and image tags are starting points and should be reviewed
before the first public release.
See the [roadmap](docs/roadmap.md) for the ordered checklist toward PHP Ship
feature parity.

## Development

Run the scaffold tests from this repository:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

To run the framework integration tests with a reachable Docker daemon and
permission to bind localhost port 8000:

```sh
SHIP_DOCKER_TEST=1 PYTHONPATH=src python -m unittest discover -s tests -p test_frameworks.py -v
```

The same flag enables the named-instance and external-network test in
`tests/test_phase3_docker.py` and the live service checks in
`tests/test_service_catalog_docker.py`, including database persistence,
Selenium, Playwright, and S3 storage. These tests create and remove
temporary Docker containers, volumes, images, and networks.
