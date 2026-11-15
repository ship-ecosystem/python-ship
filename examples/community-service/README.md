# Example HTTP service extension

This directory is an independently installable Python distribution. It
registers the `example-http` entry point in `python_ship.services` and uses
the `example` service group. Install it in the same environment as the `ship`
CLI. The service runs a small HTTP server in Docker and gives the application
`EXAMPLE_HTTP_URL`.

Select it in `ship.json`:

```json
{
  "framework": "generic",
  "app": "main:app",
  "extensions": ["example-http"],
  "services": {"example": "example-http"}
}
```

Both development and production Compose models contain the service. The
release archive includes its Python image, so the deployment machine does not
need this extension package.
