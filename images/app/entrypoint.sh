#!/bin/sh
# Runs the given command (API, worker or a one-off admin task); with none, starts the API.
set -e
cd /app
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec uvicorn gwashub_api.main:app --app-dir api --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
