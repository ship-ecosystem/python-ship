#!/bin/sh
# Run from a fresh host that has Docker, Compose, curl, and a downloaded release.
set -eu

release_dir=${1:?Usage: sh boot_release_fixture.sh RELEASE_DIRECTORY}
cd "$release_dir"
test ! -e main.py
test ! -e ship.json
test -f .env
test -f deploy-commands.sh

for archive in images/*.tar; do
    docker load -i "$archive"
done

cleanup() {
    docker compose down --volumes
}
trap cleanup EXIT

wait_http() {
    attempt=0
    while [ "$attempt" -lt 30 ]; do
        response=$(curl --fail --silent http://127.0.0.1:18080/ 2>/dev/null) &&
            [ "$response" = deployed ] && return 0
        attempt=$((attempt + 1))
        sleep 2
    done
    echo 'The released app did not return the deployed database value.' >&2
    return 1
}

sh deploy-commands.sh
docker compose up -d --wait
wait_http

count=$(docker compose exec -T postgres psql -U app -d app -Atc "SELECT count(*) FROM ship_release_probe WHERE value = 'deployed'")
test "$count" = 1

docker compose restart app
wait_http

echo 'Clean-runner release boot, deploy, HTTP, database, and restart checks passed.'
