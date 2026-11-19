#!/usr/bin/env bash
# Prepare the shared host for GWASHub (docs/SHARED_HOST.md): the gwashub user, its folders, the RDS CA bundle, and a check that
# the host is fit for it. Run once, then whenever wanted: every step is safe to repeat. It changes only the gwashub user,
# /opt/gwashub and /etc/gwashub, and only reads the rest.
#
#   GWASHUB_SSH_TARGET=ec2-user@<host> ./devtools/shared_host_setup.sh broad
#
# Needs SSH to the host and passwordless sudo there.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME=${1:?usage: shared_host_setup.sh ENV}
# shellcheck source=devtools/lib/shared_host_common.sh
source devtools/lib/shared_host_common.sh
API_PORT=$(cfg api_port)

echo "Setting up the shared host for $ENV_NAME ($GWASHUB_SSH_TARGET), API port $API_PORT"
remote sudo -n true || die "Cannot SSH to $GWASHUB_SSH_TARGET without prompts, or sudo there needs a password"

# The script runs as root on the host; the API port is its $1.
remote sudo bash -s -- "$API_PORT" <<'EOF'
set -euo pipefail
api_port=$1

echo "== the gwashub user and folders"
if id gwashub >/dev/null 2>&1; then
  echo "  user gwashub exists"
else
  useradd --system --shell /sbin/nologin --home-dir /opt/gwashub --no-create-home gwashub
  echo "  created user gwashub"
fi
install -d -o gwashub -g gwashub -m 755 /opt/gwashub /opt/gwashub/releases /opt/gwashub/data /opt/gwashub/data/home /opt/gwashub/data/mpl
install -d -o gwashub -g gwashub -m 700 /etc/gwashub
echo "  /opt/gwashub, releases, data/{home,mpl}: gwashub, 755; /etc/gwashub: gwashub, 700"

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

echo "== the RDS CA bundle"
curl -fsSL --max-time 60 -o "$scratch/bundle.pem" https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
grep -q "BEGIN CERTIFICATE" "$scratch/bundle.pem" || { echo "The download is not a certificate bundle" >&2; exit 1; }
install -o gwashub -g gwashub -m 644 "$scratch/bundle.pem" /opt/gwashub/rds-global-bundle.pem
echo "  /opt/gwashub/rds-global-bundle.pem (644)"

echo "== the host"
echo "  OS: $(. /etc/os-release && echo "$PRETTY_NAME")"
echo "  $(systemctl --version | sed -n 1p)"
if [ ! -x /usr/bin/python3.12 ]; then
  echo "/usr/bin/python3.12 is missing: install Python 3.12, with its venv and pip, on the host" >&2
  exit 1
fi
echo "  $(/usr/bin/python3.12 --version)"
if ! { /usr/bin/python3.12 -m venv "$scratch/venv" && "$scratch/venv/bin/pip" --version >/dev/null; }; then
  echo "/usr/bin/python3.12 cannot make a virtualenv with pip: install its venv and pip packages" >&2
  exit 1
fi
echo "  python3.12 makes virtualenvs with pip"
free -m
df -h /opt
echo "  $(nproc) CPUs"

echo "== port $api_port"
if ss -ltn | awk -v port="$api_port" 'NR > 1 { n = split($4, a, ":"); if (a[n] == port) found = 1 } END { exit !found }'; then
  if systemctl is-active --quiet gwashub-api; then
    echo "  in use by gwashub-api, which is this deployment"
  else
    echo "Port $api_port is already in use on the host, and not by gwashub-api: choose another api_port in infra/gwashub_infra/config.py" >&2
    ss -ltnp | grep ":$api_port " >&2 || true
    exit 1
  fi
else
  echo "  free"
fi
echo "The host is ready for GWASHub."
EOF
