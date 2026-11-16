# Development and deployment operations

## HTTPS and reverse proxies

Ship's production app listens on port 8000 inside its Docker network and does
not publish that port unless `publish_ports` is true in `ship.json`. A reverse
proxy on the same Docker network can connect to `app:8000`. A proxy on the
host can use `publish_ports` and `SHIP_PRODUCTION_PORT`; restrict that host
port with a firewall or a local bind override. Configure the proxy to
terminate TLS and pass the original host and scheme. Configure trusted hosts,
proxy headers, HTTPS redirects, and secure cookies in the application itself.
Ship does not generate certificates or a reverse proxy. Verify the proxy and
app with a real HTTPS request before directing public traffic to them.
For example, a host-installed Caddy instance can proxy a published production
port with this Caddyfile after DNS points at the host:

```caddyfile
example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Set `publish_ports` to `true`, set `SHIP_PRODUCTION_PORT=8000` in the release
environment, and allow public access only to the proxy's HTTP and HTTPS
ports. Use your own domain in place of `example.com`.

## Backups and restoration

PostgreSQL, MySQL, Redis, Meilisearch, and S3-compatible storage use named
Docker volumes. A release archive contains images and configuration, **not**
the contents of those volumes. Back up each stateful service and its metadata
before upgrades. Garage uses separate data and metadata volumes; keep those
snapshots together. Record the release tag and service image versions with
each backup.

For PostgreSQL, create a logical database dump from the deployed release
directory, where `docker-compose.yml` and `.env` live:

```sh
docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > postgres.dump
```

Store the dump securely. To restore into a **prepared, empty** database with
matching credentials, use `pg_restore` from the database service, for example:

```sh
docker compose exec -T postgres sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < postgres.dump
```

For MySQL, make and restore a logical dump with the selected application's
credentials:

```sh
docker compose exec -T mysql sh -c 'exec mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' > mysql.sql
docker compose exec -T mysql sh -c 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' < mysql.sql
```

Restore the second command into a prepared empty database, and protect the
dump because it contains application data. For Redis,
Meilisearch, and storage, use their supported export or snapshot procedure,
or stop the service and snapshot **all** its volumes consistently. Restore
into new volumes with the same service version before starting the app. Test
a full restore on an isolated host; a successful backup command alone does
not prove that the app can use the recovered data.

`ship down --volumes` deletes the development stack's named volumes. It is
not a backup operation.

## Frontend hot reload

Select `--frontend node` when initializing a project with a frontend build
script. In development run `ship npm install`, then
`ship npm run dev -- --host 0.0.0.0`. The Node container publishes its HMR
port to `127.0.0.1:5173`; change the host port with `SHIP_HMR_PORT` in
`.env`. Point the browser at that local port as required by your frontend
tool. For production assets, set `node_assets` to `true`, provide a
`package-lock.json` and a `build` script writing to `dist/`, then refresh an
older Dockerfile with `ship init --force` and run `ship build`.

## File ownership and sync

On Linux, the development app container uses the host user's numeric UID
and GID, so files written into the bind mounted project remain editable.
Production uses UID 10001. On macOS and Windows, Docker Desktop handles bind
mount ownership.

For macOS or Windows development with [Mutagen](https://mutagen.io/documentation/introduction/installation/),
install its CLI on the host, set `SHIP_MUTAGEN=1` in the shell, and run
`ship up`. Ship synchronizes the app and named process containers, waits for
the first sync, and terminates its project sessions on `ship down`.
Virtual environments, caches, build artifacts, `.env` files, and VCS data
are excluded. Dependencies are installed in the app image during build;
run `ship up` again after dependency changes. If sync fails, inspect the
Mutagen CLI output and run `ship up` again after fixing the cause.

The [configuration guide](configuration.md) explains service and environment
settings. The [deployment guide](deployment.md) gives the release procedure.
