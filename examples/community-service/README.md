# Example HTTP service

An independently installable distribution that registers `example-http` in the
`python_ship.services` entry point group, in its own `example` group. Install
it into the same environment as the `ship` command:

```sh
uv tool install python-ship --with ./examples/community-service
```

Select it in `ship.json`:

```json
{
    "framework": "generic",
    "app": "main:app",
    "services": {"example": "example-http"},
    "extensions": ["example-http"]
}
```

The service runs a small HTTP server and gives the app `EXAMPLE_HTTP_URL`. It
reads its Compose service name from the `ServiceContext`, so `serviceNames`
renames apply to it like any built-in service. Both Compose models include it,
and a release archives its image, so the deployment machine doesn't need this
package.
