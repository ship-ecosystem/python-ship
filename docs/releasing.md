# Releasing python-ship

Releases go to PyPI through a [trusted publisher](https://docs.pypi.org/trusted-publishers/):
no API token is stored anywhere. The publisher on PyPI must match this
repository exactly: project `python-ship`, owner `ship-ecosystem`, repository
`python-ship`, workflow `publish-pypi.yml`, environment `pypi`. The `pypi`
environment in the repository settings requires a maintainer's approval
before the upload job runs.

Tags follow PHP Ship's convention: `v` plus the version (`v0.1.0`), on a
commit on `main`. The package version has one source,
`src/python_ship/__init__.py`; `pyproject.toml` reads it.

## Steps

1. On a branch, set `__version__` in `src/python_ship/__init__.py`, and move
   the `Unreleased` changelog entries under `## [X.Y.Z] - YYYY-MM-DD`.
2. Run the local checks and the Docker suites:

   ```sh
   python scripts/check_local.py
   SHIP_DOCKER_TEST=1 SHIP_PRODUCTION_DOCKER_TEST=1 SHIP_RELEASE_DOCKER_TEST=1 \
       python -m unittest discover -s tests -t . -p 'test_docker_*.py'
   RELEASE_TAG=vX.Y.Z python scripts/check_release.py
   ```

3. Merge to `main` and wait for every workflow on that commit to pass.
4. Tag the merged commit and push the tag:

   ```sh
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

5. The tag starts `publish-pypi.yml`. Its first job refuses to continue
   unless the tag matches the version and the changelog, the commit is on
   `main`, and the quality, Docker integration and portable-release workflows
   all succeeded for that commit. It then rebuilds and checks the wheel and
   sdist from the tag.
6. Approve the `pypi` deployment in the workflow run. The upload job publishes
   with PyPI attestations, and the last job creates the GitHub release with
   the changelog section and both distributions attached.
7. Check the release in a clean environment:

   ```sh
   uv tool install --python 3.13 python-ship==X.Y.Z
   ship --version
   ```

PyPI releases are permanent: a mistake needs a new version, never a
re-upload. If the workflow fails before the upload job, delete the tag
(`git push --delete origin vX.Y.Z`), fix the problem, and tag again.

## Raising the security floor

When python.org ships a security release, update `MINIMUM_PATCHED` in
`src/python_ship/security.py` (and the table in the README), confirm the new
releases are on Docker Hub (`docker run --rm python:3.13-slim python -V`), and
cut a patch release. Projects pick up the new floor on their next build
without republishing their Dockerfile.
