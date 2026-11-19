#!/usr/bin/env bash
# Run a GWASHub admin command on the shared host, as the gwashub user with the API's settings (docs/SHARED_HOST.md). Replaces
# aws_admin.sh's one-off ECS tasks on this kind of deployment.
#
#   GWASHUB_SSH_TARGET=ec2-user@<host> ./devtools/shared_host_admin.sh broad users
#   GWASHUB_SSH_TARGET=ec2-user@<host> ./devtools/shared_host_admin.sh broad invite someone@uni.ac.uk "Full Name" --institution "Uni"
#   GWASHUB_SSH_TARGET=ec2-user@<host> ./devtools/shared_host_admin.sh broad add-member someone@uni.ac.uk hermes3 reviewer
#   GWASHUB_SSH_TARGET=ec2-user@<host> ./devtools/shared_host_admin.sh broad add-resources s3://BUCKET/resources/shared/_manifests/T.json
#
# The arguments reach `python -m gwashub_api.admin` exactly as typed here: spaces and quotes survive. `db-readonly-user` refuses
# on its own, as the env file has no GWASHUB_DB_READONLY_SECRET.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME=${1:?usage: shared_host_admin.sh ENV COMMAND [ARGS...]}; shift
[ "$#" -ge 1 ] || { echo "usage: shared_host_admin.sh $ENV_NAME COMMAND [ARGS...]" >&2; exit 1; }
# shellcheck source=devtools/lib/shared_host_common.sh
source devtools/lib/shared_host_common.sh
exec ssh -t "$GWASHUB_SSH_TARGET" "sudo -u gwashub /opt/gwashub/current/infra/shared_host/gwashub-run python -m gwashub_api.admin $(printf '%q ' "$@")"
