#!/usr/bin/env bash
# Run a GWASHub admin command on AWS, as a one-off ECS task with the API's settings and permissions.
#
#   ./devtools/aws_admin.sh dev users
#   ./devtools/aws_admin.sh dev invite someone@uni.ac.uk "Full Name" --institution "Uni"
#   ./devtools/aws_admin.sh dev add-member someone@uni.ac.uk hermes3 reviewer
#   ./devtools/aws_admin.sh dev seed-demo        # demo projects + synthetic resources (empty database only)
#   ./devtools/aws_admin.sh dev add-resources s3://BUCKET/resources/shared/_manifests/T.json  # see aws_upload_resources.py
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME=${1:?usage: aws_admin.sh ENV COMMAND [ARGS...]}; shift
export AWS_PROFILE=${AWS_PROFILE:-gwashub-$ENV_NAME} AWS_REGION=${AWS_REGION:-eu-west-2}
OUT=infra/cdk-outputs-$ENV_NAME.json
get() { python3 -c "import json,sys;print(json.load(open('$OUT'))['Gwashub-$ENV_NAME-App']['Out$1'])"; }
CLUSTER=$(get ClusterName); FAMILY=$(get ApiTaskFamily); SUBNETS=$(get PrivateSubnets); SG=$(get AppSecurityGroup); LOGS=$(get LogGroup)

if [ "${1:-}" = "seed-demo" ]; then
  CMD='["sh","-c","python -m devtools.synth --out /tmp/synthetic >/dev/null && GWASHUB_SHARED_RESOURCES=/tmp/synthetic/resources python -m devtools.seed"]'
else
  CMD=$(python3 -c 'import json,sys;print(json.dumps(["python","-m","gwashub_api.admin",*sys.argv[1:]]))' "$@")
fi
# seed-demo generates a synthetic genome in memory, so it gets a bigger one-off task.
SIZE='{}'; [ "${1:-}" = "seed-demo" ] && SIZE='{"cpu":"2048","memory":"8192"}'
OVERRIDES=$(python3 -c 'import json,sys;print(json.dumps({**json.loads(sys.argv[2]),"containerOverrides":[{"name":"api","command":json.loads(sys.argv[1])}]}))' "$CMD" "$SIZE")
TASK=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$FAMILY" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --overrides "$OVERRIDES" --started-by gwashub-admin --query 'tasks[0].taskArn' --output text)
echo "Running on AWS (task ${TASK##*/}); this takes about a minute..."
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK"
CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" --query 'tasks[0].containers[0].exitCode' --output text)
aws logs get-log-events --log-group-name "$LOGS" --log-stream-name "api/api/${TASK##*/}" --start-from-head \
  --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n'
[ "$CODE" = "0" ] || { echo "Exit code $CODE"; exit 1; }
