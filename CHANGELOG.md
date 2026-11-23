# Changelog

All notable changes to python-ship are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/): before 1.0, a minor release
may change the extension contracts or `ship.json`, and says how here.

## [Unreleased]

### Added

- PHP workflow and shared-spec parity: canonical ship-release metadata, digest-pinned registry
  images, project-owned archives, source/platform/image IDs, settings names, HTTP endpoints,
  volumes, deploy argv and console metadata.
- Explicit `--force`/`--no-env`, stable release identity, manifest-last publication and prior
  artifact preservation after assembly failure.
- MariaDB, MongoDB, SQL Server, Valkey, Memcached, Elasticsearch, Typesense, Centrifugo and
  Cypress. The catalog has 23 optional services; Flower is deliberately excluded.
- HTTP process objects, multiple external networks and renamed additional instances.
- Keyboard-driven setup, grouped selections, back navigation, final preview, automation flags
  and append-only development/production environment initialization.
- Optional adapter/service release, process-suggestion and HTTP capabilities; Flask factories.
- Frontend npm/pnpm/Yarn pins, frozen asset installs, switching/recovery, on-demand browser tests,
  explicit public build arguments and temporary BuildKit credentials.
- Offline canonical schemas in wheel/sdist, schema drift tooling and prepared conformance CI.

### Changed

- `DB_DATABASE`/`DB_USERNAME` align with PHP; Redis-compatible providers share connection settings.
- Production uses the explicit stable project name instead of appending `-production`.
- PostgreSQL follows the PHP 18 image family and its data mount layout.
- Declared processes run in production only; Celery Beat must be selected explicitly.
- uv development dependencies remain available in development and are pruned for production.
- Non-interactive init never invokes a uv installer prompt; doctor does not rewrite Compose.

Existing data and projects need the migration review in [docs/parity-report.md](docs/parity-report.md).
All implementation commits and verification in this pass are local; no push/publication occurred.

## [0.1.0] - 2026-11-16

The first public release.

### Added

- The `ship` command, with the same command set as PHP Ship: `init`, `up`,
  `down`, `exec`, `shell`, `logs`, `db [instance]`, `config:test`, `build`,
  `release`, plus `doctor`, `npm`, `uv`, `--version`, and framework commands
  such as Django's `manage`.
- An interactive `ship init` that detects Django and FastAPI, suggests the
  ASGI target, and asks for each service group, named instances, the Silo
  image and the Python version; every answer also has a flag, and
  `--no-interaction` never prompts.
- Built-in adapters for any ASGI or WSGI callable, Django, FastAPI and Flask;
  Uvicorn, Hypercorn, Gunicorn and Granian servers, with `ship init` offering
  only the servers that can run the app's interface.
- Celery (worker and beat from the app image) and RabbitMQ services.
- A JSON Schema for ship.json, shared with PHP Ship.
- Fourteen built-in services: PostgreSQL (`postgres`, or `pgsql`), MySQL, Redis, RabbitMQ, Celery, Mailpit, Meilisearch,
  Garage, SeaweedFS, RustFS, Silo, Selenium, Playwright and Node, with named
  instances, custom service names and an external network.
- Production images (non-root, no uv or pip) and portable releases that run
  with Docker alone, with deploy commands and deduplicated image archives.
- A Python security floor: end-of-life branches and releases with known
  vulnerabilities are refused, both in `ship.json` and in the image build.
- Typed extension contracts for community services, adapters and ASGI
  servers, loaded from entry points.
- Optional Mutagen file sync for Windows and macOS, covering every container
  that mounts the project, the Node frontend included.
- Vite HMR through the Node container, verified against a real Vite dev
  server: `ship init` prints the config it needs, and the watcher polls on
  Windows bind mounts.

[0.1.0]: https://github.com/ship-ecosystem/python-ship/releases/tag/v0.1.0
