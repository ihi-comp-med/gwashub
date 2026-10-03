#!/usr/bin/env bash
# Open a private tunnel from this Mac to the GWASHub database, for PyCharm / DataGrip / psql.
#
#   ./devtools/aws_db_tunnel.sh dev            # localhost:5433 → the dev database (Ctrl+C to close)
#   ./devtools/aws_db_tunnel.sh dev 6543       # another local port
#
# Goes through the jump host with AWS Session Manager (no SSH, no open ports); needs the Session
# Manager plugin: brew install --cask session-manager-plugin. Connect as gwashub_readonly (read only);
# the script prints its password.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME=${1:-dev}; PORT=${2:-5433}
export AWS_PROFILE=${AWS_PROFILE:-gwashub-$ENV_NAME} AWS_REGION=${AWS_REGION:-eu-west-2}
OUT=infra/cdk-outputs-$ENV_NAME.json
get() { python3 -c "import json;print(json.load(open('$OUT'))['Gwashub-$ENV_NAME-$1']['Out$2'])"; }
command -v session-manager-plugin >/dev/null || { echo "Install the Session Manager plugin first: brew install --cask session-manager-plugin"; exit 1; }
JUMP=$(get App DbJumpInstance); HOST=$(get Data DbEndpoint)
PW=$(aws secretsmanager get-secret-value --secret-id "$(get Data DbReadonlySecret)" --query SecretString --output text)
cat <<MSG
Database tunnel open while this runs (Ctrl+C to close). In PyCharm: Database → + → Data Source → PostgreSQL
  Host: localhost   Port: $PORT   Database: gwashub   SSL mode: require
  User: gwashub_readonly   Password: $PW
MSG
exec aws ssm start-session --target "$JUMP" --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "host=$HOST,portNumber=5432,localPortNumber=$PORT"
