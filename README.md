# Python Ship

An early scaffold for a framework-agnostic Docker development environment for
Python web apps, inspired by [PHP Ship](https://github.com/php-ship/ship).
This is a separate Python package and repository. Plain ASGI, Django, and
FastAPI apps use the same public adapter contract; infrastructure services use
a separate contract.

## Install

From this checkout, install into a Python 3.11+ virtual environment:

```sh
python -m pip install -e .
```

The distribution name is provisionally `python-ship`, the import name is
`python_ship`, and the installed command is `ship`. The intended repository is
`github.com/python-ship/ship`. PyPI project ownership has not yet been claimed.

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
uses it to serve the app. Installing `python-ship` also installs Uvicorn in the
host Python environment used by the CLI. The app can declare its own
dependencies separately when needed.

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
different localhost port. A project `.env` is loaded into the app container when present.
`compose.override.yaml` is applied when present. Use
`ship logs`, `ship shell`, `ship exec`, and `ship down --volumes` as needed.
Use `ship --project-directory /path/to/project COMMAND` to run any development
command from another working directory; the override is resolved from that
project root for `up`, `down`, `logs`, `shell`, `exec`, `manage`, and `db`.
`ship up` waits for the expected containers, their healthchecks, and a reachable
app port before reporting success. If startup fails, inspect `ship logs`.

Running `ship init` again preserves the existing selections, extensions, and
Dockerfile. Ship records the version of its published Dockerfile and warns when
it differs from the installed package. Use `ship init --force` to refresh that
Dockerfile. `ship config:test` collects independent configuration errors without
starting Docker. The CLI uses Typer, including shell completion. Arguments after
`ship exec SERVICE` and `ship manage` pass through to the container command.

The application must read the injected `DB_*` or `REDIS_URL` variables in its
own settings. Django and FastAPI projects declare those framework libraries in
their own dependencies. Ship supplies Uvicorn; the Dockerfile uses `uv.lock`
when present, otherwise installs `requirements.txt` or the project from
`pyproject.toml` using uv. A plain ASGI app can have none of these files.

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
Python library in the app if tests use its client. Alternatively, select
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
mailpit = "ship_mailpit:MailpitService"
```

The project then opts into each plugin by entry point name in `ship.json`:

```json
{
  "framework": "flask",
  "app": "app:app",
  "services": {"mail": "mailpit"},
  "extensions": ["flask", "mailpit"]
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

## Current scope

This scaffold implements development Compose generation, lifecycle commands,
generic/Django/FastAPI adapters, a growing built-in service catalog, and
plugin loading. Production builds and portable releases, broader dependency
manager support, alternate web servers, and remaining Docker integration checks
are next work.
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
`tests/test_phase3_docker.py` and the MySQL, Mailpit, Meilisearch, and Node
checks in `tests/test_service_catalog_docker.py`. The same file has optional
Playwright and S3 service checks. These tests create and remove
temporary Docker containers, volumes, images, and networks.
