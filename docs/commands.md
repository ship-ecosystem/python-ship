# Command reference

Run commands in the app directory or prefix them with
`ship --project-directory /path/to/project`. Use `ship COMMAND --help` for
current option details. `ship.json` selects the framework, app import target,
server, services, and extensions. See the [quick start](quickstart.md) for a
first project.

| Command | Effect |
| --- | --- |
| `ship init --app main:app` | Create `ship.json`, `ship/Dockerfile`, and ignore rules. Detects a built-in framework when possible. |
| `ship init --force` | Refresh Ship's published Dockerfile while preserving existing project selections. |
| `ship config:test` | Check config, dependencies, service settings, and development and production Compose models without starting Docker. |
| `ship up` | Generate development Compose, build images, start the stack, and wait for health and HTTP readiness. |
| `ship down [--volumes]` | Stop the stack; `--volumes` also removes named data volumes. |
| `ship logs [SERVICE] [-f]` | Show or follow Compose logs. |
| `ship doctor` | Report versions, Dockerfile freshness, and container health. |
| `ship shell [SERVICE]` | Open `sh` in a running service; defaults to `app`. |
| `ship exec SERVICE COMMAND...` | Run a command in a running service. |
| `ship manage COMMAND...` | Run Django's `manage.py` in the app container. |
| `ship db [--service NAME]` | Open a selected database service's shell. |
| `ship npm COMMAND...` | Run npm in the selected Node frontend service. |
| `ship uv COMMAND...` | Run supported project dependency commands in a temporary Docker container. |
| `ship build` | Generate production Compose and build project-owned production images. |
| `ship release --tag TAG` | Build and export a Docker-only release under `dist/ship/TAG/`. |

`ship init` accepts `--framework`, `--server`, `--python`, and service options
`--database`, `--cache`, `--mail`, `--search`, `--storage`, `--testing`, and
`--frontend`. `--node-assets` enables the production npm asset stage. Installed
community packages are selected by entry point names in `ship.json`; see the
[extension guide](extensions.md).

`ship uv` supports metadata operations such as `add` and `lock`. It rejects
`sync`, `run`, `pip`, `python`, and `tool` because their environment changes
would disappear when the temporary container exits. `ship up` installs the
dependencies into the app image. `ship exec` and `ship manage` pass their
remaining arguments directly through to the container.

Development exposes the app on `127.0.0.1:${SHIP_APP_PORT:-8000}`. Set
`SHIP_APP_PORT` in `.env`. Development `.env` is available to the app container.
Run `ship down --volumes` only when you intend to delete service data.

Production `ship build` reads `.env.production`; `ship release` requires that
file even for apps without infrastructure credentials. The production model
does not expose an app port unless `publish_ports` is true in `ship.json`.
See the [deployment guide](deployment.md) for release steps and secret handling.
