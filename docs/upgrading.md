# Upgrading an existing project

Back up persistent data and check which migrations apply before rebuilding an
existing stack.

- **Database settings.** Use `DB_DATABASE` and `DB_USERNAME` in application
  configuration and environment files. Update callers that still read the former
  `DB_NAME` or `DB_USER` settings.
- **Compose project name.** Production uses the explicit `name` in `ship.json`.
  If a deployment used the old `-production` suffix, stop the old stack and
  adopt or migrate its volumes before starting the new project. Development and
  production stacks on the same daemon need distinct project names.
- **PostgreSQL.** The bundled image uses PostgreSQL 18 and its data mount layout.
  Migrate a PostgreSQL 17 database with a supported upgrade or dump and restore.
- **Background processes.** Declared `processes` run in production. Use a
  development override or run the command explicitly in development. The Celery
  service starts a worker. Add a Beat process when the application needs scheduling.
- **Release manifests.** Deployment tools should read the
  [shared release contract](https://github.com/ship-ecosystem/spec/blob/main/docs/release-contract.md).
  In particular, image entries use `ref`, and images that the release pins by
  digest require registry access on the deployment host.
- **Generated files.** Review local template edits before running `ship init
  --force`. Ship preserves existing environment values. Fill any newly required
  production settings before building the release.
