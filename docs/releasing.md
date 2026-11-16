# Publishing Python Ship

PyPI publishing uses a trusted GitHub publisher. Its configured values must
match this repository: project `python-ship`, owner `ship-ecosystem`, repository
`python-ship`, workflow file `publish-pypi.yml`, and GitHub environment `pypi`.
The workflow keeps OIDC permission only in the publishing job; it does not
store a PyPI API token.

Before releasing, create the `pypi` environment under the GitHub repository's
Settings → Environments and require a maintainer review for deployments. The
PyPI publisher form does not configure GitHub's environment protection.

Choose a new version, then set it in both `pyproject.toml` and
`src/python_ship/__init__.py`. Run `python scripts/check_local.py`, review the
wheel and source distribution results, and commit the version change. The
release workflow also repeats those checks from the tagged commit.

Tag the reviewed commit with `v` followed by the exact package version. For
the current development version, that would be `v0.1.0.dev5`:

```sh
RELEASE_TAG=v0.1.0.dev5 python scripts/check_release.py
git tag v0.1.0.dev5
git push origin v0.1.0.dev5
```

The tag push starts `.github/workflows/publish-pypi.yml`. Its build job checks
that the tag matches both version declarations, runs the local quality and
package checks, builds the artifacts, and passes them to a separate publishing
job. After approving the `pypi` environment deployment in GitHub, verify the
published version and metadata on PyPI and install the uploaded artifact in a
fresh environment. A pending PyPI publisher creates the project on its first
successful upload; its name is not reserved before then.

Do not create the tag until the desired version and release commit are final.
PyPI releases are permanent, and changing the files afterward requires a new
version.
