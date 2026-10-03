#!/usr/bin/env bash
# Interim single-server AWS deployment (docs/AWS_MINIMAL.md): build the app image, push it to ECR,
# create/update the CloudFormation stack (infra/minimal.yaml) and restart the app on the instance.
#
#   aws login --profile gwashub-mgmt      # one-off setup: docs/AWS_MINIMAL.md
#   ./devtools/aws_deploy.sh                      # profile gwashub-dev, eu-west-2, <ip>.sslip.io
#   GWASHUB_DOMAIN=gwashub.example.org ./devtools/aws_deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_PROFILE=${AWS_PROFILE:-gwashub-dev} AWS_REGION=${AWS_REGION:-eu-west-2}
STACK=${GWASHUB_STACK:-gwashub}
REPO_NAME=gwashub/app

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY=$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com
echo "Deploying stack $STACK to account $ACCOUNT ($AWS_REGION) with profile $AWS_PROFILE"

aws ecr describe-repositories --repository-names $REPO_NAME >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name $REPO_NAME --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability IMMUTABLE >/dev/null
TAG=$(date -u +%Y%m%d-%H%M%S)
IMAGE=$REGISTRY/$REPO_NAME:$TAG
docker build --platform linux/arm64 -f images/app/Dockerfile -t "$IMAGE" .
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"
docker push "$IMAGE"

aws cloudformation deploy --stack-name "$STACK" --template-file infra/minimal.yaml --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset --parameter-overrides ImageUri="$IMAGE" DomainName="${GWASHUB_DOMAIN:-}"
out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
INSTANCE=$(out InstanceId)

# Tell the instance to pull the new image (after first-boot setup, if it is still running).
if [ "$(aws ssm describe-instance-information --filters Key=InstanceIds,Values="$INSTANCE" --query 'length(InstanceInformationList)' --output text)" = "1" ]; then
  CMD=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
    --parameters 'commands=["cloud-init status --wait >/dev/null || true","/usr/local/bin/gwashub-update"]' --query Command.CommandId --output text)
  aws ssm wait command-executed --command-id "$CMD" --instance-id "$INSTANCE" || \
    aws ssm get-command-invocation --command-id "$CMD" --instance-id "$INSTANCE" --query StandardErrorContent --output text
fi
cat <<MSG

Site: $(out Url)   (a new instance takes about 5 minutes to install Docker, seed the demo and get a certificate)
Set a password before signing in:
  aws ssm start-session --target $INSTANCE --profile $AWS_PROFILE
  sudo docker exec -it app python -m gwashub_api.admin set-password demo@gwashub.org
MSG
