# Example Starlette adapter

An independently installable distribution that registers `example-starlette`
in the `python_ship.adapters` entry point group, without any change to Ship
itself. Install it into the same environment as the `ship` command:

```sh
uv tool install python-ship --with ./examples/community-adapter
```

The app declares `starlette` in its own `requirements.txt`, so the Docker image
runs without this host-side package. Select the adapter in `ship.json`:

```json
{
    "framework": "example-starlette",
    "app": "main:app",
    "extensions": ["example-starlette"]
}
```

It shows each adapter hook:

- `detect()` and `default_app()` let `ship init` pick it and suggest `main:app`.
- `interface()` says Starlette is ASGI, so `ship init` offers ASGI servers.
- `validate()` checks the import target and the dependency declaration.
- `console_commands()` adds `ship routes`, run in the app container.
- `release_commands()` imports the app at every production boot, before the
  server starts.
