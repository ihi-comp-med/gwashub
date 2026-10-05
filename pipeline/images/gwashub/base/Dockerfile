# gwashub/base: Python 3.12, the libraries the default QC steps use, PLINK 2.0 and bcftools 1.22 with htslib (both built
# from their tagged sources, so they run natively on ARM64; PLINK clumps meta-analysis lead loci, and both build the
# reference files in resources/),
# and the GWASHub runner with every step in pipeline/qc_steps. QC jobs on AWS Batch run in this image (or one built
# FROM it). Build from the repo root (devtools/aws_cdk_deploy.sh builds every image in pipeline/images):
#   docker build -f pipeline/images/gwashub/base/Dockerfile -t gwashub/base .
FROM python:3.12-slim AS plink2
RUN apt-get update && apt-get install -y --no-install-recommends build-essential zlib1g-dev libopenblas-dev liblapack-dev \
        liblapacke-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
# PLINK v2.0.0-a.7.11 (1 Oct 2026), the release of the published binaries, from github.com/chrchang/plink-ng.
RUN curl -fsSL -o plink-ng.tar.gz https://github.com/chrchang/plink-ng/archive/refs/tags/v2.0.0-a.7.11.tar.gz \
    && echo "6b83f2ff0a2d59ed9693c7270935d59893729f451831e60fc7b646d91042ddce  plink-ng.tar.gz" | sha256sum -c - \
    && tar xzf plink-ng.tar.gz && cd plink-ng-2.0.0-a.7.11/2.0/build_dynamic && make -j"$(nproc)" \
    && cp plink2 /usr/local/bin/plink2 && /usr/local/bin/plink2 --version

FROM python:3.12-slim AS bcftools
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates bzip2 \
        zlib1g-dev libbz2-dev liblzma-dev libcurl4-openssl-dev libdeflate-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o bcftools.tar.bz2 https://github.com/samtools/bcftools/releases/download/1.22/bcftools-1.22.tar.bz2 \
    && echo "f2ab9e2f605b1203a7e9cbfb0a3eb7689322297f8c34b45dc5237fe57d98489f  bcftools.tar.bz2" | sha256sum -c - \
    && tar xjf bcftools.tar.bz2 && cd bcftools-1.22 && ./configure --prefix=/opt/bcftools && make -j"$(nproc)" && make install \
    && cd htslib-1.22 && make install prefix=/opt/bcftools

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git libopenblas0-pthread liblapacke zstd \
        zlib1g libbz2-1.0 liblzma5 libcurl4 libdeflate0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 gwashub
COPY --from=plink2 /usr/local/bin/plink2 /usr/local/bin/plink2
COPY --from=bcftools /opt/bcftools /opt/bcftools
ENV PATH=/opt/bcftools/bin:$PATH
RUN plink2 --version && bcftools --version | head -1 && bgzip --version | head -1
COPY pipeline/images/gwashub/base/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY pipeline/ pipeline/
ENV PYTHONPATH=/app:/app/pipeline/qc_steps MPLCONFIGDIR=/tmp/mpl HOME=/tmp
USER gwashub
