#!/usr/bin/env bash
# Start the local stack: API on :8000 and the web app on :3000 (proxying /api).
# First run:  ./devtools/dev.sh --fresh   (generates synthetic data, seeds the DB, uploads the demo datasets)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || { /opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
[ -d web/node_modules ] || (cd web && npm install)
# QC steps run in their images (R, bcftools, gwas2vcf and LDSC aren't on this machine); build any that are missing.
MISSING=$($PY -c "from pipeline.runner.catalog import load_images; from pipeline.runner.executor import missing_local_images; print(' '.join(missing_local_images(load_images())))")
[ -z "$MISSING" ] || ./devtools/build_images.sh $MISSING
export GWASHUB_STEP_RUNNER=docker
if [ "${1:-}" = "--fresh" ]; then
  $PY -m devtools.synth
  $PY -m devtools.seed
fi
export MPLCONFIGDIR=/tmp/gwashub-mpl GWASHUB_WORKERS=4
.venv/bin/uvicorn gwashub_api.main:app --app-dir api --port 8000 &
API=$!
trap 'kill $API 2>/dev/null' EXIT
until curl -sf localhost:8000/api/health >/dev/null; do sleep 0.5; done
if [ "${1:-}" = "--fresh" ]; then $PY -m devtools.e2e; fi
echo "API on http://localhost:8000  ·  web on http://localhost:3000  (sign in as any listed user)"
cd web && npx nuxt dev --port 3000
