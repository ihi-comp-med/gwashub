#!/usr/bin/env bash
# Deploy GWASHub to AWS with CDK (docs/AWS_FULL.md): build the website, the app image and every
# pipeline image (pipeline/images/<owner>/<image>), push them to ECR, then deploy the Network, Data
# and App stacks. Pipeline images are pinned by digest in the Batch job definitions.
#
#   ./devtools/aws_cdk_deploy.sh            # dev (profile gwashub-dev)
#   ./devtools/aws_cdk_deploy.sh prod       # prod (profile gwashub-prod)
#   GWASHUB_IMAGE_TAG=<tag> ./devtools/aws_cdk_deploy.sh dev   # reuse an image already in ECR
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME=${1:-dev}
export AWS_PROFILE=${AWS_PROFILE:-gwashub-$ENV_NAME} AWS_REGION=${AWS_REGION:-eu-west-2}
export JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION=1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY=$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com
REPO=gwashub/app
TAG=${GWASHUB_IMAGE_TAG:-$(date -u +%Y%m%d-%H%M%S)}
echo "Deploying $ENV_NAME to account $ACCOUNT ($AWS_REGION), image tag $TAG"

echo "== website"
(cd web && npx nuxi generate >/dev/null)

echo "== image"
if [ -z "${GWASHUB_IMAGE_TAG:-}" ]; then
aws ecr describe-repositories --repository-names $REPO >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name $REPO --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE >/dev/null
docker build --platform linux/arm64 -f images/app/Dockerfile -t "$REGISTRY/$REPO:$TAG" .
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
docker push -q "$REGISTRY/$REPO:$TAG"
fi

echo "== pipeline images"
PIPELINE_IMAGES="{"
for REF in $(.venv/bin/python -c "from pipeline.runner.catalog import build_order, load_images; print(' '.join(build_order(load_images())))"); do
  IREPO=gwashub-images/$REF
  aws ecr describe-repositories --repository-names "$IREPO" >/dev/null 2>&1 || \
    aws ecr create-repository --repository-name "$IREPO" --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE >/dev/null
  # Tagged locally as <owner>/<image> too, so images built FROM gwashub/base find it.
  if ! aws ecr describe-images --repository-name "$IREPO" --image-ids imageTag="$TAG" >/dev/null 2>&1; then
    docker build -q --platform linux/arm64 -f "pipeline/images/$REF/Dockerfile" -t "$REF" -t "$REGISTRY/$IREPO:$TAG" . >/dev/null
    docker push -q "$REGISTRY/$IREPO:$TAG" >/dev/null
  fi
  DIGEST=$(aws ecr describe-images --repository-name "$IREPO" --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)
  echo "  $REF $DIGEST"
  PIPELINE_IMAGES="$PIPELINE_IMAGES\"$REF\": \"$DIGEST\","
done
PIPELINE_IMAGES="${PIPELINE_IMAGES%,}}"

echo "== infrastructure"
[ -x infra/.venv/bin/python ] || { python3 -m venv infra/.venv && infra/.venv/bin/pip install -q -r infra/requirements.txt; }
# Service-linked roles that first-time ECS/Batch/Spot use needs (no-ops once they exist).
for svc in ecs.amazonaws.com batch.amazonaws.com spot.amazonaws.com; do
  aws iam create-service-linked-role --aws-service-name $svc >/dev/null 2>&1 || true
done
cd infra
aws cloudformation describe-stacks --stack-name CDKToolkit >/dev/null 2>&1 || npx --yes aws-cdk@2 bootstrap -c env="$ENV_NAME" -c image_tag="$TAG" -c pipeline_images="$PIPELINE_IMAGES" "aws://$ACCOUNT/$AWS_REGION"
npx --yes aws-cdk@2 deploy --all -c env="$ENV_NAME" -c image_tag="$TAG" -c pipeline_images="$PIPELINE_IMAGES" --require-approval never --outputs-file "cdk-outputs-$ENV_NAME.json"
URL=$(python3 -c "import json;d=json.load(open('cdk-outputs-$ENV_NAME.json'));print(d['Gwashub-$ENV_NAME-App']['OutSiteUrl'])")
echo
echo "Site: $URL"
echo "Admin commands: ./devtools/aws_admin.sh $ENV_NAME users   (see docs/AWS_FULL.md)"
