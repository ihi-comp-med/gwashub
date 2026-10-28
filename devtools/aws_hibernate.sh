#!/usr/bin/env bash
# Hibernate GWASHub on AWS (costs near zero, all data kept) and wake it again. See "Hibernating" in docs/AWS_FULL.md.
#
#   ./devtools/aws_hibernate.sh dev status    # awake or hibernating, and what is running
#   ./devtools/aws_hibernate.sh dev sleep     # holding page up; NAT, load balancer, API, worker, jump host off; database stopped
#   ./devtools/aws_hibernate.sh dev wake      # database started, everything back as it was
#
# Both sleep and wake redeploy the image that is running now (no rebuild): the same code goes to sleep and wakes up.
# The website is built from this working copy, as in every deploy.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME=${1:?usage: aws_hibernate.sh ENV status|sleep|wake}; ACTION=${2:?usage: aws_hibernate.sh ENV status|sleep|wake}
export AWS_PROFILE=${AWS_PROFILE:-gwashub-$ENV_NAME} AWS_REGION=${AWS_REGION:-eu-west-2}
OUT=infra/cdk-outputs-$ENV_NAME.json
get() { python3 -c "import json;print(json.load(open('$OUT'))['Gwashub-$ENV_NAME-$1']['Out$2'])"; }
DB=$(get Data DbEndpoint); DB=${DB%%.*}  # the instance identifier is the endpoint's first label
SCHEDULE=gwashub-$ENV_NAME-keep-db-stopped

db_status() { aws rds describe-db-instances --db-instance-identifier "$DB" --query 'DBInstances[0].DBInstanceStatus' --output text; }
# The image the API runs now (its task definition is kept while hibernating).
running_tag() {
  local image; image=$(aws ecs describe-task-definition --task-definition "$(get App ApiTaskFamily)" \
    --query 'taskDefinition.containerDefinitions[0].image' --output text)
  echo "${image##*:}"
}
active_jobs() {
  local q s n=0
  for q in gwashub-$ENV_NAME gwashub-$ENV_NAME-x86 gwashub-$ENV_NAME-ondemand gwashub-$ENV_NAME-ondemand-x86; do
    for s in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
      n=$((n + $(aws batch list-jobs --job-queue "$q" --job-status $s --query 'length(jobSummaryList)' --output text 2>/dev/null || echo 0)))
    done
  done
  echo $n
}

case "$ACTION" in
status)
  NAT=$(aws ec2 describe-nat-gateways --filter Name=state,Values=available,pending --query 'length(NatGateways)' --output text)
  TASKS=$(aws ecs list-tasks --cluster "$(get App ClusterName)" --query 'length(taskArns)' --output text)
  aws scheduler get-schedule --name "$SCHEDULE" >/dev/null 2>&1 && KEEP=on || KEEP=off
  echo "Database:        $(db_status)"
  echo "NAT gateways:    $NAT"
  echo "API and worker:  $TASKS running"
  echo "Keep-stopped:    $KEEP"
  echo "QC/meta jobs:    $(active_jobs) waiting or running"
  echo "Deployed as:     $( [ "$(get App Hibernating 2>/dev/null)" = yes ] && echo hibernating || echo awake ) (per $OUT)"
  ;;
sleep)
  JOBS=$(active_jobs)
  [ "$JOBS" = 0 ] || { echo "$JOBS QC or meta-analysis jobs are waiting or running: let them finish (or stop them) first."; exit 1; }
  TAG=$(running_tag)
  cat <<MSG
This takes GWASHub $ENV_NAME offline: every page shows the holding page until you wake it.
Kept: all files (S3), the database (stopped, with a snapshot), user accounts, images, settings.
Removed until waking: the NAT gateway, load balancer, API and worker, and the database jump host.
MSG
  read -r -p "Type $ENV_NAME to hibernate: " ok; [ "$ok" = "$ENV_NAME" ] || { echo "Not hibernating."; exit 1; }
  GWASHUB_HIBERNATE=1 GWASHUB_IMAGE_TAG=$TAG ./devtools/aws_cdk_deploy.sh "$ENV_NAME"
  if [ "$(db_status)" = available ]; then
    SNAP=gwashub-$ENV_NAME-hibernate-$(date -u +%Y%m%d-%H%M%S)
    aws rds stop-db-instance --db-instance-identifier "$DB" --db-snapshot-identifier "$SNAP" >/dev/null
    echo "Database stopping (snapshot $SNAP first). A daily schedule keeps it stopped."
  fi
  echo "GWASHub $ENV_NAME is hibernating. Wake it with: ./devtools/aws_hibernate.sh $ENV_NAME wake"
  ;;
wake)
  # The keep-stopped schedule goes first, so it can't stop the database while the deploy brings the services up
  # (the deploy then removes it from the stack).
  aws scheduler delete-schedule --name "$SCHEDULE" >/dev/null 2>&1 || true
  case "$(db_status)" in
    available) ;;
    stopped) echo "Starting the database (about 5 minutes)..."; aws rds start-db-instance --db-instance-identifier "$DB" >/dev/null
             aws rds wait db-instance-available --db-instance-identifier "$DB" ;;
    starting) echo "The database is starting..."; aws rds wait db-instance-available --db-instance-identifier "$DB" ;;
    *) echo "The database is $(db_status): try again in a few minutes."; exit 1 ;;
  esac
  GWASHUB_IMAGE_TAG=$(running_tag) ./devtools/aws_cdk_deploy.sh "$ENV_NAME"
  echo "GWASHub $ENV_NAME is awake."
  ;;
*) echo "usage: aws_hibernate.sh ENV status|sleep|wake"; exit 1 ;;
esac
