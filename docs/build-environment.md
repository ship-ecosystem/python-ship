# Build settings

Runtime `.env` files never enter the image build. Declare public values and
private credentials separately:

```json
{
  "build": {
    "publicEnv": {"VITE_API_URL": "https://api.example.com"},
    "secrets": ["UV_INDEX_TOKEN"],
    "npmrc": true
  }
}
```

Public values become Docker build arguments. They may be compiled into browser
assets; put no secrets there. Each private name reads `.ship-secrets/NAME` through
a required BuildKit secret mount and is exposed only to dependency/build RUNs.
`npmrc` mounts `.ship-secrets/npmrc` for npm/pnpm dependency operations. Yarn registry authentication should use environment references in .yarnrc.yml and explicit secret names. Neither the
secret directory nor these mounts reaches the production runtime or release.
Private Python indexes can use uv's environment-variable configuration through
these mounts; ordinary client credentials remain runtime settings.

Run `ship init --force` after changing the set of build variables, secret names,
Node branch or asset mode, after reviewing template edits. Changing only a public
value does not require a template refresh. `config:test` and builds reject missing
secret files and refuse frontend-public names in the secret list.

Frontend package.json records an exact `packageManager` pin. Keep one authoritative
lockfile. npm uses `ci`, pnpm uses `--frozen-lockfile`, and modern Yarn uses
`--immutable` with `nodeLinker: node-modules`. Yarn Classic is also accepted.
`nodeAssets` copies `dist/` into the Python runtime; configure the application's
static serving separately. Ship does not invent a frontend project or install SDKs.

Use `ship package-manager TOOL[@VERSION] --yes` only after starting the frontend
container. It snapshots recorded manifest/lock/config files and dependencies before
replacement. A failed install restores these; external effects of arbitrary
application lifecycle scripts cannot be undone. Rebuild with `ship up` afterward.
