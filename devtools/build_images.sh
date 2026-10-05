#!/usr/bin/env bash
# Build every pipeline image (pipeline/images/<owner>/<image>) locally, tagged <owner>/<image>, each for the
# platform its image.yaml names (ARM64 by default; x86-64 images run under emulation on Apple silicon).
# Local QC runs use them when GWASHUB_STEP_RUNNER=docker (devtools/dev.sh and the tests set this).
#   ./devtools/build_images.sh               # all images, in dependency order
#   ./devtools/build_images.sh gwashub/vcf   # just these
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p local/logs
ALL=$(.venv/bin/python -c "from pipeline.runner.catalog import build_order, load_images; i = load_images(); print(' '.join(f'{r}={i[r][\"platform\"]}' for r in build_order(i)))")
for LINE in $ALL; do
  REF=${LINE%%=*}; PLATFORM=${LINE#*=}
  if [ $# -gt 0 ] && [[ ! " $* " == *" $REF "* ]]; then continue; fi
  LOG="local/logs/build_${REF//\//_}.log"
  echo "== $REF ($PLATFORM)"
  if ! docker build --platform "$PLATFORM" -f "pipeline/images/$REF/Dockerfile" -t "$REF" . >"$LOG" 2>&1; then
    tail -30 "$LOG"
    echo "Building $REF failed; full log in $LOG" >&2
    exit 1
  fi
done
