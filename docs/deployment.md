# Production deployment

Ship uses the same `ship.json` for development and production. Production
builds use the `production` Dockerfile stage, include installed app
dependencies and the selected ASGI server, and omit the development source
mount. The app image runs as UID 10001.

## Prepare

Run `ship init --force` if Ship reports that the project's published
Dockerfile is older than the installed CLI. Create `.env.production` with the
real credentials required by your selected services. Keep this file out of
Git. Run `ship config:test` to validate both Compose models and required
production values before building.

Production does not publish the app port by default. To expose it, set
`"publish_ports": true` in `ship.json`; the host port comes from
`SHIP_PRODUCTION_PORT` and defaults to 8000. Put a TLS terminating reverse
proxy in front of the app and restrict the published port to the intended
network. Configure your application itself for trusted hosts, forwarded
headers, secure cookies, and HTTPS URLs as appropriate for its framework.

`ship build` writes `ship/compose.production.json` and builds local
project-owned images. This is useful for checking the production image on the
build machine. `ship release --tag 1.2.0` also exports the images and creates
`dist/ship/1.2.0/` for deployment. A tag must be valid as a Docker image tag.

## Deploy a release

Transfer the **entire** `dist/ship/TAG/` directory to a host with Docker and
the Compose plugin. It contains the runtime Compose file, `release.json`,
image archives, a private `.env`, and a generated README with exact commands.
The target host does not need Python, uv, Ship, or your source checkout.

From the release directory, load each archive listed in its README:

```sh
docker load -i images/app.tar
```

Other selected services may have additional archives. If you configured
`deploy_commands` in `ship.json`, run `sh deploy-commands.sh` **once for this
deployment**, before starting the app. It starts and waits for required
infrastructure, then runs the commands in temporary app containers and stops
on the first failure. For example:

```json
"deploy_commands": [["python", "manage.py", "migrate", "--noinput"]]
```

Ship does not run migrations implicitly. A container restart does not rerun
deployment commands. Start the app and inspect it:

```sh
docker compose up -d
docker compose ps
docker compose logs app
```

When using a reverse proxy, route traffic to the published production port or
attach the proxy to the relevant Docker network. Plan persistent volumes and
backups for selected databases and storage services before moving live data.
Do not treat the image archives as database backups. Back up service volumes
and test restoration separately.

## Secrets and access

The release `.env` contains production secrets. On POSIX, Ship writes it with
owner-only access and restricts the release directory. Preserve those
permissions when transferring it; on Windows, restrict access with filesystem
ACLs. `dist/ship/` is ignored by Git. Review the generated Compose file and
service image choices before exposing a deployment publicly.

The [command reference](commands.md) covers local operations. The
[extension guide](extensions.md) explains how community service and adapter
packages affect a production model.
