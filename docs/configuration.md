# Project configuration

`ship init` writes `ship.json` in the application root. Edit it and run
`ship config:test` before starting or releasing the stack. The file has one
required field, `app`, containing an ASGI import target such as `main:app`.
The default framework is `generic`, which works with a plain Python ASGI
callable. Django and FastAPI are built-in adapters; installed packages can
provide more [adapters and services](extensions.md).

```json
{
  "name": "example",
  "framework": "generic",
  "app": "main:app",
  "python": "3.13",
  "server": "uvicorn",
  "services": {"database": "postgres", "cache": "redis"},
  "processes": {"worker": ["python", "worker.py"]},
  "deploy_commands": [["python", "migrate.py"]]
}
```

| Field | Default | Purpose |
| --- | --- | --- |
| `name` | Directory-derived | Compose project name; use a distinct name for concurrent projects. |
| `framework` | `generic` | Adapter entry point name, including `django` and `fastapi`. |
| `app` | Required | Import target for the ASGI application. |
| `python` | `3.13` | Minor Python version used in the app image and `ship uv` container. |
| `server` | `uvicorn` | ASGI server key; `hypercorn` is also built in. |
| `services` | `{}` | One service per group, for example `database`, `cache`, `mail`, `search`, `storage`, `testing`, or `frontend`. |
| `instances` | `{}` | Additional named service selections. |
| `networks` | `{}` | Alias to an existing external Docker network name. |
| `extensions` | `[]` | Installed adapter, service, or ASGI server entry point names. |
| `silo_image` | `distroless` | Silo storage image variant: `distroless` or `standard`. |
| `publish_ports` | `false` | Publish the production app port on the host. |
| `processes` | `{}` | Additional app-image services, each with a command argument list. |
| `django_collectstatic` | `false` | Run Django `collectstatic --noinput` at startup. |
| `node_assets` | `false` | Build npm assets into the production image. |
| `deploy_commands` | `[]` | Commands run once by the release deploy script before app startup. |

For another database or cache, add an `instances` entry:

```json
"instances": {
  "analytics": {"group": "database", "service": "postgres", "name": "analytics-db"}
}
```

Its default environment prefix is `ANALYTICS_`; override it with `prefix`.
An instance can also specify a `network` alias from `networks`. Create the
external Docker network before `ship up`. Instance IDs, service names, and
environment prefixes must be unique. `ship db --service analytics` opens that
database's shell.

## Environment files

Development reads `.env`; production reads `.env.production`. Ship does not
commit either file. The app receives the selected environment file and the
variables injected by its services. Development defaults are for local use;
set real production credentials in `.env.production`. `ship config:test`
reports missing production values for the services you selected.

| Variable | Used for |
| --- | --- |
| `SHIP_APP_PORT` | Development app host port; defaults to 8000 and binds to `127.0.0.1`. |
| `SHIP_PRODUCTION_PORT` | Production app host port when `publish_ports` is true; defaults to 8000. |
| `SHIP_HMR_PORT` | Node frontend HMR host port; defaults to 5173 in development. |
| `MAILPIT_WEB_PORT` | Mailpit web UI host port; defaults to 8025 in development. |
| `SILO_CONSOLE_PORT` | Silo console host port; defaults to 9001 in development. |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL or MySQL application credentials. |
| `DB_ROOT_PASSWORD` | MySQL root credential; separate from the application user. |
| `MEILISEARCH_KEY` | Meilisearch master key. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET` | S3-compatible storage credentials and bucket. |
| `GARAGE_RPC_SECRET`, `GARAGE_ADMIN_TOKEN` | Garage server secrets. |

For a named instance, prefix its variables, such as
`ANALYTICS_DB_PASSWORD`. The app receives corresponding connection variables
such as `ANALYTICS_DB_HOST`. PostgreSQL and MySQL inject `DB_*`; Redis injects
`REDIS_URL`; storage injects `AWS_*`. Your application must read these
variables and install its own database, cache, or S3 client libraries.

`SHIP_MUTAGEN=1` is a **host shell** setting for `ship up` on macOS or
Windows; it is not an app secret. See [development operations](operations.md).

The [command reference](commands.md) lists the CLI. The
[deployment guide](deployment.md) covers production builds and releases.
