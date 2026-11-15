# Example Starlette adapter

This independently installable distribution registers `example-starlette` in
`python_ship.adapters`. Install it in the same environment as the `ship` CLI;
the application declares `starlette` in its own `requirements.txt`, so the
Docker image can run without the host-side adapter package.

Select it in `ship.json`:

```json
{
  "framework": "example-starlette",
  "app": "main:app",
  "extensions": ["example-starlette"]
}
```

The adapter validates its import target and dependency declaration, enables
reload in development, and uses Uvicorn's explicit lifespan mode in
production after checking that Starlette imports. No core Ship source changes
are needed to load it.
