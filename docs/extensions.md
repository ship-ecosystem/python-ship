# Community extensions

Ship loads explicitly selected Python entry points from distributions installed
beside the `ship` CLI. The app can remain framework free. An adapter describes
framework commands, a service adds Compose services and app settings, and a
server supplies ASGI commands.

## Install and select

An extension distribution declares one of these entry point groups:

```toml
[project.entry-points."python_ship.adapters"]
my-framework = "my_package:MyAdapter"

[project.entry-points."python_ship.services"]
my-service = "my_package:MyService"

[project.entry-points."python_ship.servers"]
my-server = "my_package:MyServer"
```

Each entry point must resolve to a class callable without arguments. Its entry
point name must be unique across all three groups. The instance's `key` is the
value used in `framework`, a service group, or `server` in `ship.json`; the
entry point name goes in `extensions`. They may differ. For example:

```json
{
  "framework": "my-framework",
  "app": "main:app",
  "extensions": ["my-framework", "my-service"],
  "services": {"example": "my-service"}
}
```

Install the extension into the same environment as the Ship CLI. For a
`uv tool install python-ship` installation, add a published extension with
`uv tool install --with my-extension python-ship`. For a local example, build its wheel and
install it in a shared virtual environment alongside Ship. Run `ship
config:test` to verify selection before starting Docker.

The extension package's Python code is used by the host CLI while generating
Compose. It is **not** installed into the app image. Declare application
runtime packages in the app's `pyproject.toml` or `requirements.txt`, as the
[Starlette example](../examples/community-adapter) does. A service can use an
external Docker image, as the [HTTP example](../examples/community-service)
does. Release archives contain the Compose model and required images, so the
deployment host does not need the extension distribution.

## Contracts

The [protocol definitions](../src/python_ship/contracts.py) describe the
required members. Return values must be valid for the configured app and
Docker Compose; Ship checks that members exist and are callable, then validates
the generated model.

| Type | Required fields | Required methods |
| --- | --- | --- |
| Framework adapter | `key` | `detect(project_root)`, `validate(config, project_root)`, `development_command(config)`, `production_command(config)`, `console_commands()`, `release_commands(config)` |
| Service | `key`, `group`, `label` | `compose_fragment()`, `app_environment()` |
| ASGI server | `key`, `requirement` | `development_command(target)`, `production_command(target)` |

Adapter commands and server commands are Compose command lists. Adapter
validation returns a list of problem strings, or an empty list. An adapter may
implement `server_target(config)` to allow the selected ASGI server to provide
commands. It may implement `production_startup_commands(config)` to return
commands run before the production server starts. This hook runs on **each**
container start, so use it only for safe, repeatable preparation. `release_commands`
is currently declarative and is not executed by `ship up`, `ship build`, or
`ship release`; deployment commands are configured explicitly in the project.

A service fragment is a Compose mapping with `services` and optionally
`volumes`. The environment method returns variables to add to the app.
Services may implement `compose_fragment_for(config, instance)` and
`app_environment_for(config, instance)` to use instance fields such as `name`,
`prefix`, and `network`. An optional `validate(config, instance, root)` returns
problems. An optional `database_shell(config, instance)` provides `ship db`.
Set `development_only = True` to exclude the service and its app variables
from production Compose. Ship merges app mappings and lists and reports scalar
conflicts.

Server `requirement` is a PEP 508 package requirement installed in the app
image. Server commands receive the app import target. The selected server's
key goes in `server` in `ship.json`.

## Compatibility and failures

This is a pre-1.0 extension API. Extension distributions should declare a
compatible `python-ship` range, test against each supported Ship release, and
publish a new version when the contract changes. The examples use
`python-ship>=0.1.0.dev5,<0.2`; consumers should pin tested versions in their
CLI installation. The extension's runtime package requirements belong in its
distribution metadata, while app runtime requirements belong in the app.

Missing distributions, duplicate entry point names, constructor failures,
duplicate or invalid keys, and missing methods raise `ConfigError` with the
selected extension's name. `ship init`, `config:test`, `build`, and `release`
fail before Docker when extension loading fails. Errors inside an extension's
own methods are reported while evaluating the config or Compose model; authors
should provide clear validation messages for user errors.

For working implementations and integration tests, see
[`examples/community-service`](../examples/community-service),
[`examples/community-adapter`](../examples/community-adapter), and
[`tests/test_phase8.py`](../tests/test_phase8.py).
