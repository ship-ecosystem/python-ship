# Adding a service

Every optional piece of the stack — a database, a cache, a storage backend, a
browser driver — is a class implementing
`python_ship.contracts.ServiceDefinition`. `RedisService` in
[`src/python_ship/services.py`](../src/python_ship/services.py) is the
simplest complete example to copy from.

The same class works built in or as a separate package; the only difference
is how it's registered (see [Extending without forking](#extending-without-forking)).

## Steps

1. **Write the class.** It needs three attributes and two methods:
   - `key` — the string used in `ship.json` (`"redis"`).
   - `label` — shown in `ship init`'s picker.
   - `group` — the single-select group it belongs to: `database`, `cache`,
     `storage`, `search`, `mail`, `testing`, `frontend`, or a new group name.
     `ship init` asks about a new group automatically.
   - `compose_fragment(context)` — the Compose service(s) this contributes,
     keyed by service name. Name your own service `context.service_name`.
   - `app_environment(context)` — variables injected into the app.

   ```python
   from python_ship.contracts import ServiceContext


   class RabbitMQService:
       key = "rabbitmq"
       label = "RabbitMQ"
       group = "queue"

       def compose_fragment(self, context: ServiceContext) -> dict:
           return {context.service_name: {
               "image": "rabbitmq:4.2.1-alpine",
               "environment": {
                   "RABBITMQ_DEFAULT_USER": context.setting("RABBITMQ_USER", "app"),
                   "RABBITMQ_DEFAULT_PASS": context.credential("RABBITMQ_PASSWORD", "ship"),
               },
               "volumes": [f"{context.resource_name}_data:/var/lib/rabbitmq"],
               "healthcheck": {"test": ["CMD", "rabbitmq-diagnostics", "-q", "ping"],
                               "interval": "5s", "timeout": "5s", "retries": 12},
           }}

       def app_environment(self, context: ServiceContext) -> dict[str, str]:
           prefix = context.env_prefix
           return {
               f"{prefix}AMQP_URL": (
                   f"amqp://{context.setting('RABBITMQ_USER', 'app')}:"
                   f"{context.credential('RABBITMQ_PASSWORD', 'ship')}@{context.service_name}:5672/"
               ),
           }
   ```

2. **Register it.** A built-in service goes into `BUILTIN_SERVICES` in
   `services.py`. A package registers an entry point instead.

3. **Test it.** `tests/test_services.py` shows the unit pattern: build a
   `ServiceContext` and assert on the fragment and variables in both
   environments. Add a live check to `tests/test_docker_services.py` that
   starts the service with `ship up` and uses it from the app container — a
   valid Compose file doesn't prove the container works.

## What `ServiceContext` gives you

| Member | Meaning |
|---|---|
| `environment`, `production` | Which model is being built. |
| `service_name` | Your final Compose service name, after `serviceNames` and instance naming. Use it for hostnames you inject. |
| `resource_name` | A name that stays stable across renames (`key`, or `key-instance`). Use it for named volumes. |
| `instance_name` | `None` for the default selection; the `additionalServices` name otherwise. |
| `env_prefix` | `""`, or `"ANALYTICS_"` for an instance named `analytics`. Prefix every variable with it. |
| `credential(name, default)` | `${PREFIX_NAME:-default}` in development; `${PREFIX_NAME:?...}` in production, so Compose refuses to start without a real value and `ship config:test` reports it. Use it for every secret. |
| `setting(name, default)` | `${PREFIX_NAME:-default}` in every environment, for non-secret settings. |
| `config`, `project_root` | The whole `ShipConfig` (for a setting such as `siloImage`) and the project directory. |

## Things that will bite you

- **Return `{}` to be absent.** A development-only service (a mail catcher,
  a browser) returns an empty fragment in production. The app then gets none
  of its variables either.
- **Fragments merge; they never silently overwrite.** A fragment may also
  extend `app` (`{"app": {"environment": {...}}}`). Nested mappings merge and
  lists accumulate, but two different values for the same scalar key is a
  configuration error naming the key and the service. Your service gets
  `restart: unless-stopped`, and `app` depends on it — waiting for it to be
  healthy when it has a healthcheck.
- **Named volumes are collected automatically.** Mount
  `f"{context.resource_name}_data:/data"` and Ship declares the volume. Bind
  mounts (`./...`) are left alone in development and rejected in production.
- **Development ports bind to `127.0.0.1`.** Ship rewrites `"8080:80"` to
  `"127.0.0.1:8080:80"`, and drops ports from production unless `publishPorts`
  is set. Give every published port a variable (`${MYSERVICE_PORT:-8080}`) so
  two projects can run side by side, and publish nothing from a named
  instance by default.
- **Pin the image tag.** Every built-in image has an exact version, and
  `renovate.json` proposes upgrades. A floating tag makes builds
  unreproducible and lets an upstream change break every project overnight.
- **Write `$$` for a literal `$`.** Compose interpolates `$VAR` in every
  string of the file. A shell command meant to read a container variable
  writes `$$VAR`; see SeaweedFS's identity script.
- **Need a build context?** Garage builds from `ship/garage/`, which
  `ship init` publishes from `src/python_ship/templates/garage/`. Follow that
  pattern in `stubs.py` for a built-in service.

## Optional capabilities

- **`ship db` support.** Implement `ProvidesDatabaseShell.database_shell(context)`,
  returning the command run inside `context.service_name`. Read credentials
  from the container's own environment (`$POSTGRES_USER`), so the shell
  always matches what the container was provisioned with.
- **Early validation.** Implement `ValidatesConfiguration.validate(context)`
  to return problems before Docker is invoked. `MySqlService` uses it to
  reject `DB_USER=root`, which the MySQL image refuses to start with.

## Supporting multiple named instances

`additionalServices` adds a second instance of a service in the `database`,
`cache`, `storage`, `search` or `mail` group. Your service supports it for
free as long as it uses `context.service_name`, `context.resource_name` and
`context.env_prefix` everywhere, as every example above does. Two instances
then never share a container, a volume or a variable. A service that only
makes sense once (a browser driver, a frontend toolchain) belongs in a group
that doesn't allow instances.

## Extending without forking

A separate distribution registers the class under an entry point:

```toml
[project]
name = "ship-rabbitmq"
dependencies = ["python-ship>=0.1,<0.2"]

[project.entry-points."python_ship.services"]
rabbitmq = "ship_rabbitmq:RabbitMQService"
```

A project installs it next to the CLI (`uv tool install python-ship --with
ship-rabbitmq`) and selects it by entry point name in `ship.json`'s
`extensions`. [`examples/community-service`](../examples/community-service)
is a complete, tested package.

The contracts are the public API and follow semantic versioning: before 1.0,
a minor release may change them, and the changelog says how. Declare a
`python-ship` range that excludes the next minor version, and test against
each release you support. Failures are reported as configuration errors
naming the extension: not installed, two entry points with the same name, an
exception while loading, a missing method or attribute, or a key that is
already taken.

## Adding a framework adapter

A `FrameworkAdapter` is the other extension point, one per framework.
`DjangoAdapter` and `FastAPIAdapter` in [`adapters.py`](../src/python_ship/adapters.py)
are the references:

- `key`, `label` — the `ship.json` `framework` value and the picker's label.
- `detect(project_root)` — whether the project looks like yours. `ship init`
  tries adapters in order and suggests the first match, so be specific.
- `default_app(project_root)` — the ASGI target to suggest, or `None`.
- `validate(config, project_root)` — problems, as a list of messages.
  `python_ship.adapters.target_problems()` checks a `module:callable` target
  statically, without importing project code on the host.
- `server_target(config)` — the ASGI target the selected server runs.
- `console_commands()` — extra `ship <name>` proxies run in the app
  container, such as Django's `{"manage": ["python", "manage.py"]}`. They're
  registered only when your adapter is selected, and can't reuse Ship's own
  command names.
- `release_commands(config)` — commands run at **every production container
  boot**, before the server: checks and cache warming that need the real
  environment. Never migrations: with several containers, each would run
  them at once. Projects put those in `deployCommands`.

An adapter is registered under `python_ship.adapters`;
[`examples/community-adapter`](../examples/community-adapter) is a complete
Starlette adapter.

## Adding an ASGI server

An `ASGIServer` has `key`, `label`, `requirement` (one PEP 508 requirement
installed into the image) and `development_command(target)` /
`production_command(target)`, each listening on `0.0.0.0:8000`. Register it
under `python_ship.servers`; projects select it with `"server"`.
