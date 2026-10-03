#!/bin/sh
# With a command (AWS: API, worker, Batch job or admin task), run it. Without one (the interim
# single-server deployment), create the demo projects on first boot and start the API.
set -e
cd /app
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
if [ ! -f "$GWASHUB_DATA_DIR/gwashub.db" ]; then
  echo "No database yet: creating the demo projects"
  python -m devtools.synth --out /data/synthetic
  python -m devtools.seed
fi
exec uvicorn gwashub_api.main:app --app-dir api --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers 1
