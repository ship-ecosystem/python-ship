# Quick start

Python Ship runs your web app and supporting services in Docker. The `ship`
command itself runs on the host; [uv](https://docs.astral.sh/uv/getting-started/installation/)
can supply its Python runtime. Your app does not need a host Python installation.
Docker with the Compose plugin is required for app commands.

## Install the CLI

From this checkout, run `uv tool install --python 3.13 .`. Then check
`ship --help`. The PyPI distribution name is not yet claimed; the checkout is
the installation source until publication.

`ship init` needs uv on the host. If it cannot find uv, it asks permission to
install it and stops without changing the project if the answer is no or the
installer fails.

## Framework-free app

Create a directory with `main.py`:

```python
async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"Hello from Ship"})
```

Run these commands from that directory:

```sh
ship init --app main:app
ship config:test
ship up
```

Open `http://127.0.0.1:8000/`. Ship supplies Uvicorn inside the app image, so
this example needs no framework, dependency file, or host Python. `ship init`
creates `ship.json` and `ship/Dockerfile`; `ship up` builds and starts the
development stack. Run `ship logs`, `ship doctor`, and `ship down` when needed.
Set `SHIP_APP_PORT=8001` in `.env` to use a different local port.

## Django and FastAPI

For a Django project with `manage.py` and `project/asgi.py`, declare Django in
your app dependencies, then run:

```sh
ship init --framework django --app project.asgi:application --database postgres
ship config:test
ship up
ship manage migrate
```

For a FastAPI project with `app/main.py`, declare FastAPI in your app
dependencies, then run:

```sh
ship init --framework fastapi --app app.main:app
ship config:test
ship up
```

Ship accepts `uv.lock`, `requirements.txt`, an installable `pyproject.toml`, or
a dependency-free app. Use `ship uv add PACKAGE` or `ship uv lock` to update
project metadata in a Docker container. `ship up` rebuilds the app image with
new dependencies. See the [command reference](commands.md) and
[production deployment guide](deployment.md) for the next steps.
