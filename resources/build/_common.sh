# Shared helpers for resources/build/*.sh (sourced, not run).
# Built files go to $GWASHUB_RESOURCES_OUT (default local/resources/, git-ignored), downloads to its _downloads/.
# Tools run in GWASHub's own images, so nothing needs installing here: bcftools, bgzip, tabix and PLINK 2 (gwashub/base,
# native ARM64), pysam (gwashub/vcf). Build them first: ./devtools/build_images.sh gwashub/base gwashub/vcf
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${GWASHUB_RESOURCES_OUT:-$ROOT/local/resources}
DL=$OUT/_downloads
mkdir -p "$OUT" "$DL"
OUT=$(cd "$OUT" && pwd -P); DL=$(cd "$DL" && pwd -P)

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }

# fetch URL FILE [md5:HEX|sha256:HEX]: download into $DL (resumable), check the checksum when one is given.
fetch() {
  local url=$1 dest=$DL/$2 sum=${3:-}
  if [ ! -e "$dest.ok" ]; then  # the marker is written once a download finishes
    log "downloading $url"
    curl -fL --retry 5 --retry-delay 10 -C - -o "$dest" "$url"
    touch "$dest.ok"
  fi
  if [ -n "$sum" ]; then
    local algo=${sum%%:*} want=${sum#*:} got
    case $algo in
      md5) got=$( (md5sum "$dest" 2>/dev/null || md5 -r "$dest") | cut -d' ' -f1) ;;
      sha256) got=$(shasum -a 256 "$dest" | cut -d' ' -f1) ;;
      *) echo "unknown checksum type $algo" >&2; exit 1 ;;
    esac
    [ "$got" = "$want" ] || { echo "checksum mismatch for $dest: $got, expected $want" >&2; rm -f "$dest.ok"; exit 1; }
    log "checksum ok ($algo) $2"
  fi
  printf '%s' "$dest"
}

# in_image IMAGE COMMAND: run a shell command in a GWASHub image with $OUT and $DL mounted at the same paths.
in_image() {
  local image=$1; shift
  local arch; arch=$(docker image inspect --format '{{.Architecture}}' "$image")  # gwashub/vcf is x86-64 only
  docker run --rm --platform "linux/$arch" -u "$(id -u):$(id -g)" -v "$OUT:$OUT" -v "$DL:$DL" -w "$DL" \
    --entrypoint bash "$image" -c "$*"
}

# record KEY...: note what was built (sha256, size, script, date) in $OUT/BUILT.tsv, for the upload's provenance.
record() {
  local script; script=$(basename "$0")
  [ -s "$OUT/BUILT.tsv" ] || printf 'key\tsha256\tbytes\tscript\tbuilt\n' > "$OUT/BUILT.tsv"
  for key in "$@"; do
    printf '%s\t%s\t%s\t%s\t%s\n' "$key" "$(shasum -a 256 "$OUT/$key" | cut -d' ' -f1)" "$(wc -c < "$OUT/$key" | tr -d ' ')" \
      "$script" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$OUT/BUILT.tsv"
    log "built $key"
  done
}
