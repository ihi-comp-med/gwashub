#!/usr/bin/env bash
# Run the API tests against a throwaway database in Docker: MySQL 8.0, MySQL 8.4 or Postgres 16.
#   devtools/test_db.sh <mysql-8.0|mysql-8.4|postgres-16> [pytest args...]
#   devtools/test_db.sh mysql-8.0 api/tests/test_mysql.py -x
# Pytest runs api/tests, or just the files and folders you name. The container is removed when the script ends.
# The port can be changed with GWASHUB_TEST_DB_PORT (default: MySQL 3307, Postgres 5433) when something else is using it.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

case "${1:-}" in
  mysql-8.0) IMAGE=mysql:8.0.40 ;;
  mysql-8.4) IMAGE=mysql:8.4.3 ;;
  postgres-16) IMAGE=postgres:16 ;;
  *) echo "usage: $0 <mysql-8.0|mysql-8.4|postgres-16> [pytest args...]" >&2; exit 2 ;;
esac
TARGET=$1
shift

NAME="gwashub-test-$TARGET-$$"
if [ "$TARGET" = postgres-16 ]; then
  PORT=${GWASHUB_TEST_DB_PORT:-5433}
  INNER=5432
  URL="postgresql+psycopg://postgres:test@127.0.0.1:$PORT/postgres"
  RUN=(-e POSTGRES_PASSWORD=test "$IMAGE")
else
  PORT=${GWASHUB_TEST_DB_PORT:-3307}
  INNER=3306
  URL="mysql+pymysql://root:test@127.0.0.1:$PORT/gwashub_test?charset=utf8mb4"
  RUN=(-e MYSQL_ROOT_PASSWORD=test -e MYSQL_DATABASE=gwashub_test "$IMAGE"
       --character-set-server=utf8mb4 --collation-server=utf8mb4_0900_as_cs)
fi

trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:$INNER" "${RUN[@]}" >/dev/null

# The database image starts a temporary server to set itself up, then restarts: wait for the real one, over the port the tests use.
echo "Waiting for $TARGET on port $PORT ..."
for _ in $(seq 1 120); do
  if PROBE_ERR=$(GWASHUB_PROBE_URL="$URL" $PY -c "import os, sqlalchemy as sa; sa.create_engine(os.environ['GWASHUB_PROBE_URL']).connect().close()" 2>&1); then
    READY=1
    break
  fi
  sleep 1
done
if [ -z "${READY:-}" ]; then
  echo "$TARGET did not accept connections within 120 s. Last error:" >&2
  echo "$PROBE_ERR" | tail -n 3 >&2
  docker logs "$NAME" 2>&1 | tail -n 20 >&2
  exit 1
fi

TARGETS=(api/tests)
for arg in "$@"; do
  if [ -e "${arg%%::*}" ]; then TARGETS=(); fi  # a path was named: run that instead of all of api/tests
done
# (the ${...+...} form keeps an empty array from tripping `set -u` on the bash 3.2 that macOS ships)
GWASHUB_TEST_DB_URL="$URL" $PY -m pytest ${TARGETS[@]+"${TARGETS[@]}"} "$@"
