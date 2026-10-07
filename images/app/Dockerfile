# gwashub/app: the API and the worker (same image, different command; see infra/gwashub_infra/app_stack.py).
# The website is built separately and served by CloudFront; QC steps run in the pipeline images
# (pipeline/images). Build from the repo root (devtools/aws_cdk_deploy.sh does this):
#   docker build --platform linux/arm64 -f images/app/Dockerfile -t gwashub/app .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 gwashub
COPY images/app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY api/ api/
COPY pipeline/ pipeline/
COPY devtools/ devtools/
COPY validation/ validation/
COPY client/ client/
COPY practice/ practice/
COPY images/app/entrypoint.sh /entrypoint.sh
# Fail closed: without its Cognito settings the container refuses to start rather than offering the
# local dev sign-in. Everything else comes from the task definition.
ENV PYTHONPATH=/app:/app/api GWASHUB_AUTH=cognito GWASHUB_DATA_DIR=/tmp/gwashub MPLCONFIGDIR=/tmp/mpl HOME=/tmp
USER gwashub
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
