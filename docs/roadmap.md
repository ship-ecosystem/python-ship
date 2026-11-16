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
- [x] Add Selenium and Playwright browser-testing services appropriate for
  Python test suites, with the same easy-to-select role that Dusk/Selenium has
  in PHP Ship; verify both in real Docker stacks.
- [x] Support Node and frontend asset workflows, including an accessible HMR
  port, without making Node mandatory for a plain Python app.
- [x] Make the ASGI server/runtime selectable beyond Uvicorn, with a public
  extension path for community server choices. Keep the app framework
  independent from that choice.
- [x] Pin and document service image versions; test every selected service
  against a real Docker daemon.

Phase 4 complete. PostgreSQL, MySQL, and Redis kept data across container
restarts in live Docker checks. Mailpit captured SMTP mail, Meilisearch accepted
an authenticated API request, and Node served an HTTP response through its HMR
port. Garage, SeaweedFS, RustFS, and both Silo image variants passed live S3
bucket and object operations. SeaweedFS rejected unknown credentials and
accepted a secret containing a quote and backslash. Garage's build files ship
in the wheel and are republished by `ship init` when missing. Python clients
opened a framework-free app through both Selenium Chrome and Playwright
Chromium. The browser services use a `.test` network alias because browsers
force the single-label `app` host to HTTPS. Hypercorn served and reloaded a
framework-free ASGI app in Docker; built-in framework adapters and third-party
server entry points use the same server selection contract. Built-in service
image tags are documented and all catalog services have live Docker checks.

## 5. Make development portable and predictable

- [x] Test the actual uv installer accept, decline, install-failure, and
  already-installed paths on Linux, macOS, and Windows; verify its executable
  remains discoverable after installation.
- [x] Use uv correctly for a locked `uv` project, a `requirements.txt` project,
  an installable `pyproject.toml` project, and a dependency-free app; verify
  generated image behavior, not only Dockerfile text.
- [x] Provide a `ship uv` workflow for project dependency commands without
  losing Ship's supplied server when uv syncs the environment.
- [x] Handle host UID/GID in development so container-generated files are
  usable on Linux; define the equivalent behavior for Windows and macOS.
- [x] Add optional Mutagen sync for slow bind mounts on Windows/macOS; exclude
  platform-specific environments and avoid dependency-install races.
- [x] Verify two Ship projects can run at once without network, volume, or
  port collisions; explain how users choose alternate host ports.
- [x] Add service/version/stub diagnostics and clear recovery messages for
  crashed or unhealthy containers.

Phase 5 progress: an isolated `uv tool install` passed with only uv-managed
Python, so a preinstalled system Python is unnecessary for the CLI. Following
PHP Ship's model, the cross-platform CLI has a host language runtime while
the application runs in Docker. The official uv installer passed an isolated
install and executable check on Linux, macOS, and Windows in the
[host platform workflow](https://github.com/ship-ecosystem/python-ship/actions/runs/43762995074).
Consent, decline, already-installed, and failure paths also have unit coverage
on all three platforms. Four
dependency modes passed a real app-image check, including an
installable project both with and without `uv.lock`; `ship uv` now runs in an
isolated Docker image with the selected Python version, and `ship up` rebuilds
the app image. A live `ship uv add` also updated the lockfile without creating
a host `.venv` or removing Uvicorn from the rebuilt app. Linux
container-generated files retained the
host UID/GID. Docker Desktop uses its own bind-mount ownership behavior on
macOS and Windows. Two concurrent stacks passed port, network, and Redis data
isolation checks. `ship doctor` reports selected service images and container
health. Optional direct Mutagen sync passed a live Docker check: the app
started from its built image, host code edits reached the container and
triggered Uvicorn reload, no host `.venv` appeared, and `ship down` removed
the project-scoped session. The source sync omits platform-specific
environments, build output, caches, VCS directories, and `.env` files. Its
startup waits for an initial sync flush after the image has installed app
dependencies.

## 6. Build a production image and production config

- [x] Generate separate development and production Compose files from the
  same project config; production must have no source bind mount.
- [x] Build a production Python image using uv and declared locks, with a
  non-root runtime, a predictable process command, and no development tools
  or secret files baked in.
- [x] Put framework-specific production behavior in adapters: Django
  deployment checks and static collection strategy; FastAPI and generic
  ASGI startup without Django assumptions.
- [x] Support optional Node asset builds and additional long-running Python
  processes such as workers and schedulers from the application image.
- [x] Require real production credentials and validate `.env.production`
  before invoking Docker; keep production ports unpublished unless requested.
- [x] Make `config:test` build and validate both Compose models and report
  missing credentials, unsafe settings, and conflicting names together.
- [x] Verify a production HTTP request, database connection, and container
  restart for generic ASGI, Django, and FastAPI fixtures.

Phase 6 complete: `ship build` generates a separate Compose file
and project name. The app has no source bind mount or published port, and
production rejects host bind mounts from extensions. A multi-stage Dockerfile
uses uv in the builder and runs the production app as UID 10001 without uv or
pip. A live generic ASGI Docker check passed HTTP startup and verified that
`.env.production` was absent from the image. Existing projects must refresh
their published Dockerfile with `ship init --force` before using the production
target. `config:test` validates both models and lists missing or known-default
production credentials from `.env.production`; `ship build` checks them before
invoking Docker. Production ports stay unpublished unless `publish_ports` is
set. Django runs its deployment check before startup and can collect static
files when selected; generic ASGI and FastAPI start their ASGI server directly.
Optional npm asset builds ran in a Node build stage and copied `dist/` into
the Python image without carrying Node into runtime. A named worker shared
the production app image and connected to PostgreSQL. Generic ASGI, Django,
and FastAPI each passed a production HTTP request, PostgreSQL query, and
container restart. The Python CLI now matches PHP Ship's production command
pattern: `config:test` validates and `build` generates/builds; the temporary
`config:production` command was removed. Phase 7 will add image-build
deduplication and portable releases.

## 7. Produce portable releases

- [x] Implement `ship build` to build and tag each project-owned production
  image, deduplicating identical builds.
- [x] Implement `ship release --tag` with a validated tag and deterministic
  `dist/ship/<tag>/` layout: production Compose file without `build:`, image
  archives, release manifest, and deployment instructions.
- [x] Package the production environment safely, exclude secrets and previous
  releases from image build contexts, and restrict release-file permissions
  where the platform supports them.
- [x] Run deployment commands once per deploy in an app image, after required
  infrastructure starts; stop on the first failure. Include migrations only
  when the project explicitly requests them.
- [x] Boot a release on a separate clean Docker daemon or CI environment using Docker alone,
  load its images, run its deploy commands, and assert HTTP/database behavior.
- [x] Test failure and recovery for a crashed app container and a missing
  production credential.

Phase 7 progress: `ship build` groups identical build definitions under one
image tag. `ship release --tag` packages a runtime-only Compose file, private
production environment, manifest, deployment instructions, and deduplicated
archives for both project-owned and selected infrastructure images. A deploy
script runs only explicitly configured commands after infrastructure is
healthy and stops on the first failure; Django migrations are no longer
implicit. Secret and prior-release ignores are reapplied before builds, and
artifact permissions are restricted on POSIX. A release was copied to a
source-free directory; its app image tag was removed and loaded from the
archive, then Docker alone started PostgreSQL, ran the deploy command, served
an HTTP response backed by the database, restarted, and recovered from a
forced app crash. Missing credentials failed before Docker. A second boot used
a separate Docker daemon with zero starting images and containers. It loaded
the release archives, ran the deploy command, served an HTTP response backed
by PostgreSQL, verified the database row, and served again after an app
restart. The temporary daemon was removed afterward. The
[two-job CI workflow](https://github.com/ship-ecosystem/python-ship/actions/runs/43799124805)
also passed: one runner built and exported the release, and a fresh runner
loaded its archives and verified deployment, HTTP, database, and restart
behavior without a source checkout or Ship CLI.

## 8. Prove the community extension model

- [x] Provide a small third-party service example as a separate installable
  package, selected through `ship.json`, and verify it runs in Docker.
- [x] Provide a small third-party framework adapter example and verify it can
  supply commands and production behavior without changing core code.
- [x] Document the supported contracts, entry point names, version policy,
  dependency requirements, and how plugin errors are reported.
- [x] Test duplicate names, missing distributions, broken constructors,
  invalid implementations, and plugin behavior across init, build, and
  release.

Both examples build as independent wheels and load through their installed
entry points. Docker-backed checks exercised development HTTP responses,
production builds, release creation, and HTTP responses from release archives.
Publishing the distributions publicly remains part of Phase 9.

## 9. Ship and maintain the package

- [x] Add CI for supported Python versions on Linux, macOS, and Windows:
  tests, typing/lint checks, packaging checks, and Compose model checks.
- [ ] Add Docker-backed CI for generic ASGI, Django, FastAPI, selected
  services, production builds, portable releases, and Mutagen where available.
- [ ] Keep workflow permissions narrow and pin build tooling and external
  downloads; automate review of image and dependency updates.
- [x] Complete user documentation for quick start, all commands, config,
  extensions, production deployment, HTTPS/reverse proxy, backup/restore,
  frontend HMR, file ownership, and Windows/macOS sync.
- [ ] Confirm the PyPI distribution name, create the `python-ship/ship` GitHub
  repository, build wheel and source distribution, test installation from the
  artifacts, then publish the first release.

Phase 9 progress: the 15-job Linux/macOS/Windows quality matrix passed for
Python 3.11–3.15. It runs pinned lint and type checks, unit and Compose
tests, then builds and installs both wheel and source distributions in clean
environments. The Docker integration workflow covers development adapters,
selected services, production, Mutagen, and separately installed community
examples. The development, services, production, and community jobs passed
on GitHub; the corrected Mutagen job passed locally with the complete pinned
archive and its next GitHub run is queued. The portable-release boot workflow
passes on a fresh Docker-only runner. Dependabot is proposing updates for
Python dependencies, Actions, and Dockerfiles; `renovate.json` is prepared to
track images embedded in Python service definitions. User guides in `docs/`
now cover the configuration and operations listed above. The GitHub
repository and `pypi` environment exist, and a pending PyPI trusted
publisher is configured. The tag-gated OIDC publishing workflow is prepared
in `publish-pypi.yml`; no release tag or first upload has been made.

**Completion criterion:** a framework-free Python app, a Django app, and a
FastAPI app can each go from `ship init` to a verified development stack and a
portable production release. Community packages can add a service or adapter
without forking Ship. The release runs with Docker alone, and CI exercises
those paths on the supported platforms.
