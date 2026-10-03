# gwashub/base: Python 3.12, the libraries the default QC steps use, and the GWASHub runner with every
# step in pipeline/qc_steps. QC jobs on AWS Batch run in this image (or one built FROM it).
# Build from the repo root (devtools/aws_cdk_deploy.sh builds every image in pipeline/images):
#   docker build -f pipeline/images/gwashub/base/Dockerfile -t gwashub/base .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 gwashub
COPY pipeline/images/gwashub/base/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY pipeline/ pipeline/
ENV PYTHONPATH=/app:/app/pipeline/qc_steps MPLCONFIGDIR=/tmp/mpl HOME=/tmp
USER gwashub
