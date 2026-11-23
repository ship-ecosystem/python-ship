# Python Ship parity report

Completed locally on November 23, 2026. PHP reference: `0964965`. PHP leads the
workflow; ship-ecosystem/spec owns shared contracts. This pass implements Python
counterparts rather than preserving accidental differences between producers.

## Delivered

| Area | Result |
| --- | --- |
| Adapters | Exactly four built-ins: no framework, Django, FastAPI and Flask; static detection, Flask factories, adapter console commands and boot hooks |
| Servers | Uvicorn/Hypercorn for ASGI, Gunicorn for WSGI, Granian for both; compatible server selection remains independent of the framework |
| Portable services | Added MariaDB, MongoDB, SQL Server, Valkey, Memcached, Elasticsearch, Typesense, Centrifugo and Cypress; 23 providers total |
| Python tasks | Celery worker integration; Beat is an explicit production process choice; Redis/Valkey and RabbitMQ coordination |
| Configuration | Canonical schema identity, HTTP process objects, argv including empty arguments, multiple networks, renamed named instances and stable resource identities |
| Releases | Canonical ship-release metadata, source/platform/image IDs, archive hashes, owned versus digest-pinned registry images, HTTP/service/volume/console/deploy/environment metadata |
| Publication | Explicit stable name, --force and --no-env, private files, executable deploy script, manifest-last staging and previous-artifact preservation on assembly failure |
| Init | Rich selections, group checkboxes, back navigation, numbered fallback, final preview, scripted instances/processes/networks/deploy commands and clear/remove operations |
| Environment | Missing development settings and empty production requirements appended without rewriting user values/comments; adapter-required names included |
| Frontend | Exact npm/pnpm/Yarn pins, lockfile authority, frozen asset builds, switching with recorded-file/dependency recovery, selected Node branch and on-demand browser tests |
| Build settings | Explicit public values, BuildKit secret environment mounts and npmrc; credentials excluded from images/releases and source synchronization |
| Extensions | Existing service/adapter/server entry points retained; optional release description, process suggestions and HTTP metadata capabilities |
| Diagnostics | Read-only doctor, production checks, real app-port readiness and container command argument forwarding |
| Distribution | Both canonical schemas and runtime templates included in wheel/sdist; drift checker and prepared conformance workflows |

Flower is deliberately excluded. Users can provide custom services through
extensions, development overrides or independently managed infrastructure. Ship
wires its selected providers; it does not limit the application's clients or
install application SDKs/business handlers automatically.

## Local evidence

- The repository's pinned `scripts/check_local.py` passed lint, typing, all
  unit/Compose checks and installed wheel/sdist consumer checks. The suite
  discovered 292 tests: 233 ran successfully and 59 opt-in/platform tests were
  skipped. A final restricted rerun passed with 60 skips (232 executed). The skipped tests are not counted as live integration passes.
- Both canonical schemas match the local spec byte for byte, including their
  installed package copies. Generated release fixtures pass the shared complete
  folder checker, including references, Compose identities and archive hashes.
- Four live production cases passed: no-framework ASGI, Django, FastAPI with
  Hypercorn and Flask with Gunicorn. Checks include database/cache access,
  non-root runtime, absence of build tools/secrets, restarts and a real Celery task.
- All nine new portable providers passed a final Docker readiness/protocol suite.
  MariaDB, MongoDB and SQL Server executed queries as their application users;
  cache/search/WebSocket services answered their protocol checks. Cypress's
  version check supplements a separate actual browser test.
- A copied release loaded its archived application image and booted from a folder
  without source files. Deploy commands, HTTP, database access, restart and
  crash recovery passed on the final release writer/readiness revision.
- Live npm -> pnpm -> Yarn switches passed with rebuilds and exact versions.
  Cypress executed a real Electron test against the Python app. A BuildKit test
  confirmed public asset output and absence of credentials/mounts in the runtime.
  A locked uv project pruned its development dependency while retaining its server.
- A real terminal test passed back navigation, cancellation without writes and
  terminal-mode restoration. Isolated Mutagen passed initial sync, source reload,
  credential/environment exclusions and session teardown; its temporary daemon
  was stopped afterward.
- The shared-spec fixture suite passed. PHP's unit suite also passed after its
  schema refresh: 736 tests, 1,659 assertions. JS's schema/checker drift check passed.

The first provider run found a service-rename bug affecting image repository names;
it was fixed and regression-tested. An Elasticsearch fixture initially used the
wrong password. Its final authenticated check passed. This host is above the
normal Elasticsearch disk watermark, so its live fixture explicitly disables disk
allocation thresholds for that test only; production definitions retain defaults.
The first Cypress fixture served text/plain and its screenshot was root-owned;
the successful fixture serves HTML and restores ownership of its test directory.
The first frontend fixture collided with an existing port; the final fixture uses
isolated free ports. These initial failures are not represented as passes.

## Existing-project migration

Review these before upgrading an existing project:

1. Database settings use `DB_DATABASE` and `DB_USERNAME`, following PHP. Update
   application settings that read the former `DB_NAME`/`DB_USER` names.
2. Production uses the explicit stable `name`, without the old `-production`
   suffix. This changes Compose volume prefixes. Stop old stacks and explicitly
   adopt/migrate their data; do not assume a new empty volume means data was lost.
   Development and production with the same name must not run on the same daemon.
3. PostgreSQL now follows PHP's 18 family and its data mount layout. Migrate a 17
   database with the database's supported upgrade/export process; do not reuse
   its old data directory blindly. Only delete throwaway development volumes.
4. Declared `processes` are production-only. Use explicit development commands or
   an override for workers needed during development. Selecting Celery creates a
   worker; scheduling requires an explicit Beat process.
5. Old release metadata is replaced by the canonical format. Update custom
   consumers from image entries' `image` to `ref`, and read `builder`/`source` and
   the remaining shared fields. Registry services now require registry access.
6. Review template edits, then run `ship init --force` for the new template revision,
   generated entrypoint and frontend/build settings. Existing environment values
   are preserved; fill newly appended production requirements yourself.

Provider tags are reviewed per producer and are not all identical patch pins.
Common infrastructure behavior and settings converge; an image version change
still requires a deliberate upgrade and data review.

## Local commit trail

| Commit | Step |
| --- | --- |
| f8a17fa | Shared configuration, HTTP processes and networks |
| 81033b5 | Canonical portable releases |
| 305bf22 | Portable services and connection wiring |
| 56eb835 | Setup planning and environment merging |
| 10b8680 | Python capabilities and explicit scheduling |
| 0231f1f | Frontend package managers and browser command |
| d5d26dc | Build credentials and provider rename hardening |
| a397ea4 | Offline package schemas and conformance gates |
| 8beb9e1 | Automation and extension validation |
| eb13b45 | Terminal and tooling acceptance |
| b3f5b0e | Readiness, common settings and prepared CI |
| b9fcfe2 | Consistent exec/console credential preparation |
| 2e3fa2b | Credential exclusions in synchronization |
| 513209a | Sync acceptance aligned with production processes |
| c0661f2 | Public development/build setting consistency |
| be6b4c3 | Centrifugo metadata uses its shared URL setting |
| 786c235 | Shared checker applied to transferred real artifacts |

The documentation commit records the final report. Coordinated local commits in
other repositories are spec `bddc6bd` (Python factories/shared build settings), PHP
`af62aa5` and JS `64863d1` (canonical schema copies). Those producer changes are
schema-only; their application implementations were not changed.

## Remaining release gates and limits

No push, remote branch, tag, publication, deployment or workflow dispatch occurred.
No workflow was enabled. Prepared workflows still need an explicitly authorized
candidate CI run. Publish coordinated spec changes before relying on remote
schemas/drift checks, and review package release metadata separately.

This pass used local Linux Docker. It does not certify the full host/Python matrix,
every retained provider's live suite, real second-host/multi-platform transfers,
production load/high availability, backup restoration or hosted-provider TLS.
The shared release contract remains a draft pending the consuming deployment-tool
integration run. These are release/environment validation gates, not a promise
that arbitrary application configuration has been tested.
