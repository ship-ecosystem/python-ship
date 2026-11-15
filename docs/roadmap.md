# Roadmap to PHP Ship feature parity

This is the working checklist for Python Ship. The target is the **capability**
of [PHP Ship](https://github.com/php-ship/ship) today: a portable development
stack, extensible services and framework support, reliable diagnostics, and a
Docker-only production release. Python-specific choices replace PHP-specific
features; for example, ASGI servers and Python worker processes replace PHP
runtimes and Reverb.

Work through the phases in order. Check an item only after its behavior is
implemented, documented, and verified at the level the item describes. Keep
this file current in the same change that completes an item. A phase is complete
when every item in it is checked. Do not treat a unit test or a valid Compose
file as proof that containers run correctly.

## 0. Establish the package and contracts

- [x] Create `com.ship.python` as a separate Git repository with Python package
  metadata, the `ship` command, and a public README.
- [x] Define separate framework adapter and infrastructure service contracts.
- [x] Include a generic ASGI adapter plus built-in Django and FastAPI adapters.
- [x] Load explicitly selected third-party adapters and services from Python
  package entry points.
- [x] Supply Uvicorn as the initial default server and generate a Dockerfile
  that installs it with uv, including for an app with no dependency manifest.
- [x] Require uv at `ship init`; ask before installing it; cancel without
  project-file changes on refusal or installation failure. These paths have
  mocked unit coverage; real installer checks remain in phase 5.
- [x] Generate a development Compose model for a plain ASGI app, Django, or
  FastAPI; Docker Compose accepts each model with `config --quiet`.
- [x] Add initial unit tests for config, adapters, plugins, Compose generation,
  and uv confirmation. Current result: 38 passing tests with Docker and socket access.

## 1. Prove the framework-agnostic development path

- [x] Replace the scaffold's `argparse` CLI with Typer and add Typer as a
  package dependency. Keep `ship exec` and framework command proxies able to
  forward unknown flags and raw arguments unchanged; verify help, errors,
  completion, exit codes, and interactive commands before removing `argparse`.
- [x] Add a minimal, framework-free ASGI fixture with no `pyproject.toml` or
  `requirements.txt`, plus an HTTP response assertion.
- [x] Run `ship init`, `config:test`, `up`, `logs`, `exec`, `shell`, and `down`
  against that fixture with a real Docker daemon on Linux.
- [x] Verify that the image builds, uv and Uvicorn work inside it, the bind
  mount preserves the installed environment, and code changes reload.
- [x] Make `ship up` check container state, healthchecks, and the published port
  before reporting success; show actionable failures when startup fails.
- [x] Forward command arguments and exit codes exactly, including flags and
  interactive terminal behavior, through `ship exec` and proxy commands.
- [x] Support re-running `ship init` without losing existing selections,
  extensions, or hand-edited config; track published stub versions and warn
  when a stub is stale rather than replacing it silently.
- [x] Make `config:test` report all independent config and Compose problems in
  one pass without requiring Docker.

Phase 1 verified on Linux with a temporary plain ASGI project: the image built,
uv and Uvicorn ran in the container, HTTP returned 200, bind mount edits reloaded,
the interactive shell and lifecycle commands worked, a real healthcheck became
healthy, and a restarting container produced an actionable error. The full suite
passed 20 tests with socket access. The ordinary sandbox still blocks local
sockets and the Docker socket; integration checks used elevated tool access.

## 2. Prove both built-in framework adapters

- [x] Add a real Django fixture and verify development HTTP, `ship manage`,
  database access, Redis access, and useful errors for a bad ASGI target.
- [x] Add a real FastAPI fixture and verify development HTTP, reload, database
  access, Redis access, and useful errors for a bad app target.
- [x] Keep framework-specific settings and commands inside adapters; verify
  that neither built-in framework is required by a plain ASGI project.
- [x] Define adapter hooks for development command, production command,
  validation, and release-time actions without running migrations on every
  container boot.
- [x] Add documentation and tests for the app settings users must supply to
  consume `DB_*`, `REDIS_URL`, and other injected variables.

Phase 2 verified on Linux with real Django and FastAPI containers, PostgreSQL,
and Redis. Both served HTTP and queried PostgreSQL and Redis through Ship's
injected variables. Django management commands ran, FastAPI reloaded a bind
mounted edit, and invalid targets produced configuration errors. The full suite
passed 26 tests with `SHIP_DOCKER_TEST=1` and socket access.

## 3. Make Compose and extensions robust

- [x] Merge service fragments into the app and other services without erasing
  build settings, environment, mounts, ports, networks, or dependencies.
- [x] Validate service groups, duplicate keys, plugin failures, conflicting
  Compose names, invalid config types, and malformed command targets clearly.
- [x] Add named additional instances for database, cache, storage, search, and
  mail, with separate volumes, service names, and prefixed connection variables.
- [x] Support custom service names and external networks while correcting DNS
  host references and `depends_on` references.
- [x] Support the conventional Compose override file consistently for every
  development command; test path resolution from other working directories.
- [x] Add optional service capabilities for database shells and config-aware
  fragments; omit development-only services from production cleanly.
- [x] Keep development ports on `127.0.0.1` by default and isolate project
  networks and volumes with a stable, validated Compose project name.

Phase 3 verified with Compose config checks and a live Linux stack containing
two PostgreSQL and two Redis instances, an external network, an override file,
distinct volumes, DNS names, and prefixed variables. The full suite passed 38
tests with `SHIP_DOCKER_TEST=1` and socket access. Community service groups and
production omission use contract tests; their real service images belong to
later catalog and release phases.

## 4. Reach the Python service catalog

- [x] Finish PostgreSQL: persistent data, healthcheck, app connection values,
  and `ship db` using credentials from the database container itself.
- [x] Add MySQL with the same behavior and a separate root password; reject an
  invalid application root user before starting Docker.
- [x] Finish Redis persistence and healthchecks; document how Django and
  framework-free apps opt into it.
- [x] Add Mailpit and Meilisearch with correct dev/production behavior and
  required credentials where applicable.
- [x] Add S3-compatible storage choices equivalent to the PHP package's
  Garage, SeaweedFS, RustFS, and Silo support, including their provisioning,
  health, volume, image, and CPU constraints.
- [ ] Add Selenium and Playwright browser-testing services appropriate for
  Python test suites, with the same easy-to-select role that Dusk/Selenium has
  in PHP Ship; verify both in real Docker stacks.
- [x] Support Node and frontend asset workflows, including an accessible HMR
  port, without making Node mandatory for a plain Python app.
- [ ] Make the ASGI server/runtime selectable beyond Uvicorn, with a public
  extension path for community server choices. Keep the app framework
  independent from that choice.
- [ ] Pin and document service image versions; test every selected service
  against a real Docker daemon.

Phase 4 progress: PostgreSQL and Redis were exercised with Django and FastAPI
in Phase 2. MySQL, Mailpit, Meilisearch, and Node now have isolated live Docker
checks; MySQL uses separate application and root accounts, Mailpit captures an
SMTP message, Meilisearch accepts an authenticated API request, and Node serves
an HTTP response through the published HMR port. Named data volumes, health
checks, production omission, and image tags are covered by Compose model
checks. Garage, SeaweedFS, RustFS, and both Silo image variants now pass live
S3 bucket and object operations. SeaweedFS also rejects unknown credentials
and accepts a secret containing a quote and backslash. Garage's build files
ship in the wheel and are republished by `ship init` when missing. Playwright
is selectable with matching server and client versions; a
live Python client opened the framework-free app through Chromium. The browser
services use a `.test` network alias because browsers force the single-label
`app` host to HTTPS. A live Selenium check, alternate ASGI servers,
and live persistence checks remain before Phase 4 is complete.

## 5. Make development portable and predictable

- [ ] Test the actual uv installer accept, decline, install-failure, and
  already-installed paths on Linux, macOS, and Windows; verify its executable
  remains discoverable after installation.
- [ ] Use uv correctly for a locked `uv` project, a `requirements.txt` project,
  an installable `pyproject.toml` project, and a dependency-free app; verify
  generated image behavior, not only Dockerfile text.
- [ ] Provide a `ship uv` workflow for project dependency commands without
  losing Ship's supplied server when uv syncs the environment.
- [ ] Handle host UID/GID in development so container-generated files are
  usable on Linux; define the equivalent behavior for Windows and macOS.
- [ ] Add optional Mutagen sync for slow bind mounts on Windows/macOS; exclude
  platform-specific environments and avoid dependency-install races.
- [ ] Verify two Ship projects can run at once without network, volume, or
  port collisions; explain how users choose alternate host ports.
- [ ] Add service/version/stub diagnostics and clear recovery messages for
  crashed or unhealthy containers.

## 6. Build a production image and production config

- [ ] Generate separate development and production Compose files from the
  same project config; production must have no source bind mount.
- [ ] Build a production Python image using uv and declared locks, with a
  non-root runtime, a predictable process command, and no development tools
  or secret files baked in.
- [ ] Put framework-specific production behavior in adapters: Django
  deployment checks and static collection strategy; FastAPI and generic
  ASGI startup without Django assumptions.
- [ ] Support optional Node asset builds and additional long-running Python
  processes such as workers and schedulers from the application image.
- [ ] Require real production credentials and validate `.env.production`
  before invoking Docker; keep production ports unpublished unless requested.
- [ ] Make `config:test` build and validate both Compose models and report
  missing credentials, unsafe settings, and conflicting names together.
- [ ] Verify a production HTTP request, database connection, and container
  restart for generic ASGI, Django, and FastAPI fixtures.

## 7. Produce portable releases

- [ ] Implement `ship build` to build and tag each project-owned production
  image, deduplicating identical builds.
- [ ] Implement `ship release --tag` with a validated tag and deterministic
  `dist/ship/<tag>/` layout: production Compose file without `build:`, image
  archives, release manifest, and deployment instructions.
- [ ] Package the production environment safely, exclude secrets and previous
  releases from image build contexts, and restrict release-file permissions
  where the platform supports them.
- [ ] Run deployment commands once per deploy in an app image, after required
  infrastructure starts; stop on the first failure. Include migrations only
  when the project explicitly requests them.
- [ ] Boot a release on a clean machine or CI environment using Docker alone,
  load its images, run its deploy commands, and assert HTTP/database behavior.
- [ ] Test failure and recovery for a crashed app container and a missing
  production credential.

## 8. Prove the community extension model

- [ ] Publish a small third-party service example as a separate installable
  package, selected through `ship.json`, and verify it runs in Docker.
- [ ] Publish a small third-party framework adapter example and verify it can
  supply commands and production behavior without changing core code.
- [ ] Document the supported contracts, entry point names, version policy,
  dependency requirements, and how plugin errors are reported.
- [ ] Test duplicate names, missing distributions, broken constructors,
  invalid implementations, and plugin behavior across init, build, and
  release.

## 9. Ship and maintain the package

- [ ] Add CI for supported Python versions on Linux, macOS, and Windows:
  tests, typing/lint checks, packaging checks, and Compose model checks.
- [ ] Add Docker-backed CI for generic ASGI, Django, FastAPI, selected
  services, production builds, portable releases, and Mutagen where available.
- [ ] Keep workflow permissions narrow and pin build tooling and external
  downloads; automate review of image and dependency updates.
- [ ] Complete user documentation for quick start, all commands, config,
  extensions, production deployment, HTTPS/reverse proxy, backup/restore,
  frontend HMR, file ownership, and Windows/macOS sync.
- [ ] Confirm the PyPI distribution name, create the `python-ship/ship` GitHub
  repository, build wheel and source distribution, test installation from the
  artifacts, then publish the first release.

**Completion criterion:** a framework-free Python app, a Django app, and a
FastAPI app can each go from `ship init` to a verified development stack and a
portable production release. Community packages can add a service or adapter
without forking Ship. The release runs with Docker alone, and CI exercises
those paths on the supported platforms.
