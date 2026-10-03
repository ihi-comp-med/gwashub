# gwashub/app: the whole site in one container (API + QC runner + built website).
# Interim single-server deployment (see docs/AWS_MINIMAL.md). Build from the repo root:
#   docker build -f images/app/Dockerfile -t gwashub/app .
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/ ./
RUN npx nuxt prepare && npx nuxt generate

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 gwashub && mkdir /data && chown gwashub /data
COPY images/app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY api/ api/
COPY pipeline/ pipeline/
COPY devtools/ devtools/
COPY --from=web /web/.output/public /app/web
COPY images/app/entrypoint.sh /entrypoint.sh
ENV PYTHONPATH=/app:/app/api \
    GWASHUB_DATA_DIR=/data/app \
    GWASHUB_SHARED_RESOURCES=/data/synthetic/resources \
    GWASHUB_WEB_DIR=/app/web \
    GWASHUB_AUTH=password \
    MPLCONFIGDIR=/tmp/gwashub-mpl \
    GIT_AUTHOR_NAME=gwashub GIT_AUTHOR_EMAIL=gwashub@localhost \
    GIT_COMMITTER_NAME=gwashub GIT_COMMITTER_EMAIL=gwashub@localhost
USER gwashub
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
