#!/usr/bin/env bash
# Deploy GWASHub to AWS with CDK (docs/AWS_FULL.md): build the website, the app image and every
# pipeline image (pipeline/images/<owner>/<image>), push them to ECR, then deploy the Network, Data
# and App stacks. Pipeline images are pinned by digest in the Batch job definitions.
#
#   ./devtools/aws_cdk_deploy.sh            # dev (profile gwashub-dev)
#   ./devtools/aws_cdk_deploy.sh prod       # prod (profile gwashub-prod)
#   GWASHUB_IMAGE_TAG=<tag> ./devtools/aws_cdk_deploy.sh dev   # reuse an image already in ECR
#   GWASHUB_HIBERNATE=1 ...                 # hibernate: use devtools/aws_hibernate.sh, which also stops the database
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME=${1:-dev}
export AWS_PROFILE=${AWS_PROFILE:-gwashub-$ENV_NAME} AWS_REGION=${AWS_REGION:-eu-west-2}
export JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION=1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY=$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com
REPO=gwashub/app
TAG=${GWASHUB_IMAGE_TAG:-$(date -u +%Y%m%d-%H%M%S)}
HIBERNATE=${GWASHUB_HIBERNATE:-}
echo "Deploying $ENV_NAME to account $ACCOUNT ($AWS_REGION), image tag $TAG${HIBERNATE:+ (hibernating)}"

source devtools/lib/pipeline_images.sh  # LIFECYCLE, ensure_repo, push_pipeline_images (shared with shared_host_deploy.sh)

echo "== website"
(cd web && npx nuxi generate >/dev/null)
# The site's files replace the bucket's (prune): never deploy an empty or half-written build.
for f in index.html 200.html paused.html; do
  [ -f "web/.output/public/$f" ] || { echo "The website build has no $f: not deploying (rebuild with: cd web && npx nuxi generate)" >&2; exit 1; }
done

echo "== image"
if [ -z "${GWASHUB_IMAGE_TAG:-}" ]; then
ensure_repo $REPO
docker build --platform linux/arm64 -f images/app/Dockerfile -t "$REGISTRY/$REPO:$TAG" .
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
docker push -q "$REGISTRY/$REPO:$TAG"
docker rmi "$REGISTRY/$REPO:$TAG" >/dev/null  # it's in ECR now; no need to keep a copy per deploy on this machine
fi

echo "== pipeline images"
push_pipeline_images "$REGISTRY" "$TAG"

# Keep the build cache from filling the disk (it grows with every build), but big enough that the next build is fast.
for b in $(docker buildx ls --format '{{.Name}}' 2>/dev/null | sort -u); do
  docker buildx prune -f --builder "$b" --max-used-space 15gb >/dev/null 2>&1 || true
done

echo "== infrastructure"
[ -x infra/.venv/bin/python ] || { python3 -m venv infra/.venv && infra/.venv/bin/pip install -q -r infra/requirements.txt; }
# Service-linked roles that first-time ECS/Batch/Spot use needs (no-ops once they exist).
for svc in ecs.amazonaws.com batch.amazonaws.com spot.amazonaws.com; do
  aws iam create-service-linked-role --aws-service-name $svc >/dev/null 2>&1 || true
done
cd infra
aws cloudformation describe-stacks --stack-name CDKToolkit >/dev/null 2>&1 || npx --yes aws-cdk@2 bootstrap -c env="$ENV_NAME" -c image_tag="$TAG" -c pipeline_images="$PIPELINE_IMAGES" -c hibernate="$HIBERNATE" "aws://$ACCOUNT/$AWS_REGION"
npx --yes aws-cdk@2 deploy --all -c env="$ENV_NAME" -c image_tag="$TAG" -c pipeline_images="$PIPELINE_IMAGES" -c hibernate="$HIBERNATE" --require-approval never --outputs-file "cdk-outputs-$ENV_NAME.json"
URL=$(python3 -c "import json;d=json.load(open('cdk-outputs-$ENV_NAME.json'));print(d['Gwashub-$ENV_NAME-App']['OutSiteUrl'])")
echo
echo "Site: $URL"
echo "Admin commands: ./devtools/aws_admin.sh $ENV_NAME users   (see docs/AWS_FULL.md)"
