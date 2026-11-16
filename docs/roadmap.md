# Roadmap

What python-ship does today, grouped by area, and what's next. The README is
the user-facing reference; this file is the maintainer's overview and records
the non-obvious reasons behind each decision. Python-ship follows PHP Ship's
interface (commands, `ship.json` field names, the release artifact) and
replaces PHP-specific parts with Python ones: ASGI servers instead of PHP
runtimes, uv instead of Composer, `processes` workers instead of Reverb.

## What's built

### Extension points

- Three typed protocols in `contracts.py`: `ServiceDefinition`,
  `FrameworkAdapter` and `WebServer`, plus the optional
  `ProvidesDatabaseShell` and `ValidatesConfiguration`. They are the public
  API; `py.typed` ships with the package.
- Services receive a frozen `ServiceContext`: environment, final Compose
  name, stable resource name, instance name and variable prefix, plus
  `credential()` and `setting()` helpers that produce the right Compose
  expression for each environment. Everything a service needs is explicit,
  so named instances and `serviceNames` work for community services without
  any rewriting by the core.
- An empty fragment means "absent in this environment": no Compose service
  and no variables. Development-only services use it in production.
- `plugins.load_extensions()` loads only entry points named in `extensions`,
  across `python_ship.services`, `python_ship.adapters` and
  `python_ship.servers`, and checks each against its contract (key, label,
  group, required callables, optional hooks, requirement). Every failure is a
  configuration error naming the extension.
- `FrameworkAdapter.release_commands()` runs at every production boot before
  the server (PHP Ship's `releaseCommands()`); `console_commands()` become
  `ship <name>` proxies, registered only when that adapter is selected, like
  PHP Ship's `artisan`/`console`.

### Frameworks and servers

- Every adapter reports its app's interface, ASGI or WSGI; servers declare
  the interfaces they serve, and `ship init` offers only compatible ones,
  recommended first (Uvicorn for ASGI, Gunicorn for WSGI). An omitted
  `server` means the recommended one; an incompatible one is an error.
- Built-in servers: Uvicorn and Hypercorn (ASGI), Gunicorn (WSGI), Granian
  (both, with `--interface` set to match), all pinned exactly.
- Flask adapter: detects `Flask(...)`, suggests the target, sets `FLASK_APP`
  and adds `ship flask`. Django's interface follows its target (`asgi.py` or
  `wsgi.py`); a plain callable's is read from its signature.

- Built-in adapters: `generic` (any ASGI callable, no framework or manifest
  needed), `django` and `fastapi`. Each detects its project and suggests the
  ASGI target for `ship init`, and validates the target statically with
  `ast`, without importing project code on the host.
- Django: `ship manage`, `check --deploy` at every production boot, and
  `collectstatic` with `djangoCollectstatic`.
- Built-in servers: Uvicorn (default) and Hypercorn, pinned exactly. Server
  choice is independent of the framework. Production Uvicorn runs with
  `--proxy-headers` and honours `WEB_CONCURRENCY` and `FORWARDED_ALLOW_IPS`.

### Services

- 14 built-in services: PostgreSQL, MySQL, Redis, RabbitMQ, Celery, Mailpit, Meilisearch,
  Garage, SeaweedFS, RustFS, Silo, Selenium, Playwright, Node.
- Named instances (`additionalServices`) for `database`, `cache`, `storage`,
  `search` and `mail`: Compose service `{key}-{name}`, variable prefix
  `{NAME}_`, volume `{key}-{name}_data`. Same naming rule as PHP Ship.
- Every stateful service keeps data in a named volume and has a healthcheck;
  the app waits for healthy dependencies. Redis keeps RDB snapshots and an
  append-only log.
- Secrets go through `ServiceContext.credential()`: an overridable default in
  development, `${VAR:?...}` in production. `ship config:test`/`build`/
  `release` also reject a production value equal to a development default.
- MySQL: separate `DB_ROOT_PASSWORD`; the healthcheck pings `127.0.0.1`
  because a socket ping succeeds against the init-phase server;
  `DB_USER=root` is rejected up front.
- Storage: SeaweedFS generates an S3 identity file from the credentials at
  boot (its gateway is unauthenticated without one); Garage provisions its
  key and bucket and has its RPC address substituted at image build time;
  RustFS publishes no console (its console returns AccessDenied); Silo's
  `siloImage` picks the standard or distroless image, `ship init` asks with
  the CPU explanation, and named Silo instances publish their console on an
  ephemeral port.
- Browser testing: Selenium and Playwright. The app gets a `ship-app.test`
  alias because browsers force single-label hosts to HTTPS.
- Meilisearch runs with `MEILI_ENV=production` in production.
- Celery (`tasks` group) runs a worker and a beat container from the app
  image through `ServiceContext.app_process()`, with RabbitMQ (`broker`
  group, also new) or Redis as broker and Redis or RPC as result backend. In
  development the worker restarts on changes when watchfiles is installed.
- PostgreSQL accepts `pgsql` as well as `postgres`, as PHP Ship does; both
  packages write `postgres`.
- `schema/ship.schema.json` is the ship.json JSON Schema shared with PHP
  Ship, written as `$schema` by `ship init` and checked by `test_schema.py`.

### Compose generation

- `ship/docker-compose.generated.yml` and `ship/docker-compose.production.yml`
  — the same names as PHP Ship. They are JSON, which is valid YAML, behind a
  generated-file comment, so no YAML dependency is needed.
- Fragments merge into existing services; a real conflict is an error naming
  the key and the service, never a silent winner.
- `serviceNames` renames the app or a selected service; injected hostnames
  follow because services read `context.service_name`. Volumes don't, so a
  rename never orphans data. `externalNetwork` attaches the app and its
  processes to a pre-existing network.
- Collisions between renamed services, instances and process names are
  rejected before Docker.
- Development ports bind `127.0.0.1`; production publishes only the app,
  only with `publishPorts`. Every port has a variable (`APP_PORT`,
  `VITE_PORT`, `MAILPIT_WEB_PORT`, `SILO_CONSOLE_PORT`).
- Each file carries `name:`; production uses `<name>-production`, so the two
  stacks never share containers or volumes.
- The development app runs as the host UID/GID on Linux (`hostUser`, default
  on, unlike PHP Ship's root default because Python images have no fixed
  web-server user to match), and can reach `host.docker.internal`.
- `processes` share the app image, environment and networks, have no ports,
  get a 60s stop grace period, and accept a shell string (with `$` escaped
  for Compose) or an argument list.
- The Node frontend serves Vite with HMR on `VITE_PORT`; a live test drives a
  real Vite dev server's WebSocket through the published port. Under Mutagen
  every container that mounts the project is synced (Node included), and on
  Windows bind mounts Vite's watcher polls.

### Images

- One `ship/Dockerfile`: `base` → `builder` → `production`/`development`,
  plus an optional `assets` Node stage. The development target is the
  builder, so dependencies sit in `/opt/ship-venv`, outside the bind mount.
- uv installs dependencies: `uv sync --locked` with a lockfile,
  `uv pip install -r` for `requirements.txt`, `uv sync` for an unlocked
  `pyproject.toml`, nothing for a manifest-free app; then the selected server.
- Production: no uv, no pip, UID 10001, no source mount, `.env` files and
  earlier releases excluded by `.dockerignore`.
- `ship init` keeps an edited Dockerfile; `--force` replaces it.
  `ship/.ship-version` records the publishing version and every command warns
  on a mismatch.

### Security floor

- `security.MINIMUM_PATCHED` holds the first patched release per supported
  branch (3.11.18, 3.12.16, 3.13.18, 3.14.10, 3.15.2: the October 2026
  expedited releases). Config validation rejects end-of-life branches and
  vulnerable pinned releases; `ship/check-python-security-floor.py` fails an
  image build on a stale cached base image; production builds always pull.

### Commands

- `init`, `up`, `down`, `exec`, `shell`, `logs`, `db [instance]`,
  `config:test`, `build`, `release`, `doctor`, `npm`, `uv`, adapter
  commands, and `--version`. Typer provides help and shell completion; a
  bare `ship` lists commands.
- `ship init` is interactive on a terminal (framework, target, server, one
  picker per group, named instances, Silo image, Python version, each
  defaulting to the current selection) and fully scriptable with flags and
  `--no-interaction`. It reads an existing `ship.json` leniently and carries
  over every field it doesn't ask about. It validates the full model and
  checks for uv before writing anything, and merges secret and generated-file
  rules into `.gitignore` and `.dockerignore`.
- Proxies forward raw arguments and exit codes; without a terminal they pass
  `-T`, so piping SQL into `ship db` works.
- `ship up` waits for containers, healthchecks and a real HTTP answer (a TCP
  connect isn't enough: Docker's port proxy accepts before the server
  listens), and fails fast on a crashed or unhealthy container.
- `config:test` reports schema, stub, adapter, service, Compose and
  credential problems in one pass, without Docker. `doctor` reports versions
  and container health with recovery hints.
- `ship uv` runs uv metadata commands in a temporary container with the
  project's Python and refuses commands whose effects would vanish.
- The override file (`docker-compose.override.yml`, or `compose.override.yaml`)
  applies to every development command and never to production.

### Mutagen sync

- Opt in with `SHIP_MUTAGEN=1`. The app and each process get a labelled
  session, `ship up` waits for the first sync, `ship down` terminates it.
  Environments, caches, build output, `.env` files and VCS data are excluded.

### Production releases

- `ship build` builds each unique image once (the app and its processes
  share one); `ship release --tag` also exports every image, infrastructure
  included, into `dist/ship/<tag>/` with a build-free Compose file, the
  production `.env` (0600), `release.json` with SHA-256 digests, a README and
  `deploy-commands.sh`. The release is staged and only replaces an earlier
  one when every step succeeds.
- `deployCommands` start infrastructure, wait for health, run each command in
  a one-off app container, and stop at the first failure. Migrations are
  never implicit.

### Tooling

- Unit tests per component (config, security floor, adapters, Compose,
  services, plugins, CLI, init, production, release, runtime, uv, Mutagen),
  run on Linux, macOS and Windows with Python 3.11–3.15, and against the
  lowest supported Typer.
- Docker suites, run on every push to `main`, weekly, and on demand: the
  three app types in development (HTTP, database, cache, reload, `manage`,
  `db`), every built-in service live, production images for all three app
  types, releases booted from a source-free directory (deploy commands,
  restart, crash recovery), the installable examples, Mutagen, and a release
  booted on a second, clean runner.
- `scripts/check_local.py` mirrors CI; `scripts/check_package.py` builds the
  wheel and sdist and initializes a project with each.
- Workflows have read-only permissions, actions are pinned to commits,
  downloads are checksum-verified, and publishing uses PyPI trusted
  publishing behind a reviewed `pypi` environment and the full Docker suite.
  Dependabot tracks Actions, Python and Dockerfiles; Renovate tracks images
  and versions embedded in Python source.

## Next

- Publish 0.1.0 to PyPI (see [`releasing.md`](releasing.md)).
- A Flower monitoring container for Celery, and more community examples,
  once someone needs them.
- Cross-service coordination beyond what `build_compose()` computes itself
  (browser alias, `APP_URL`). Design a general mechanism when a concrete need
  appears.
