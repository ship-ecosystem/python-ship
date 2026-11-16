# Changelog

All notable changes to python-ship are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/): before 1.0, a minor release
may change the extension contracts or `ship.json`, and says how here.

## [0.1.0] - Unreleased

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
- Optional Mutagen file sync for Windows and macOS.

[0.1.0]: https://github.com/ship-ecosystem/python-ship/releases/tag/v0.1.0
