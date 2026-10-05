# gwashub/vcf: harmonisation to the reference FASTA and liftover to GRCh38 (gwashub/harmonise_liftover).
#   - the GWASHub runner and Python 3.12 libraries, as in gwashub/base
#   - bcftools 1.22 with the liftover plugin from freeseek/score db4da22 (the source of bioconda's
#     bcftools-liftover-plugin 1.22), plus bgzip and tabix
#   - a separate Python 3.7 environment (/opt/gwas2vcf) for the edited gwas2vcf that lives with the step
# x86-64 only (image.yaml): gwas2vcf's pinned Python 3.7 / pysam 0.15.2 / numpy 1.15.4 have no ARM64 builds.
# It doesn't build FROM gwashub/base because that image is ARM64; the first stage below repeats it.
#   docker build --platform linux/amd64 -f pipeline/images/gwashub/vcf/Dockerfile -t gwashub/vcf .

FROM python:3.12-slim AS bcftools
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates bzip2 \
        zlib1g-dev libbz2-dev liblzma-dev libcurl4-openssl-dev libdeflate-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o bcftools.tar.bz2 https://github.com/samtools/bcftools/releases/download/1.22/bcftools-1.22.tar.bz2 \
    && echo "f2ab9e2f605b1203a7e9cbfb0a3eb7689322297f8c34b45dc5237fe57d98489f  bcftools.tar.bz2" | sha256sum -c - \
    && curl -fsSL -o score.tar.gz https://github.com/freeseek/score/archive/db4da22.tar.gz \
    && echo "bdfcd293656a540648056f69ab240260ab7ba04288e2f7c5f2abd208e7a53302  score.tar.gz" | sha256sum -c - \
    && tar xjf bcftools.tar.bz2 && tar xzf score.tar.gz \
    && cp score-*/liftover.c bcftools-1.22/plugins/ \
    && cd bcftools-1.22 && ./configure --prefix=/opt/bcftools && make -j"$(nproc)" && make install \
    && cd htslib-1.22 && make install prefix=/opt/bcftools

FROM mambaorg/micromamba:2.0.5 AS gwas2vcf
USER root
COPY pipeline/images/gwashub/vcf/gwas2vcf-env.yaml /tmp/env.yaml
RUN micromamba create -y -p /opt/gwas2vcf -f /tmp/env.yaml && micromamba clean -afy
# vgraph at the commit gwas2vcf pins, compiled with the environment's own compilers (see its environment.yml).
ADD --checksum=sha256:0735b6142bd94f012893c32d2d54ca967e9bb07d37ee736b69780312919e0d96 \
    https://github.com/bioinformed/vgraph/archive/5866e6a686ce98e8d02f6f97f6ab025c89f123b9.tar.gz /tmp/vgraph.tar.gz
# The Python 3.7 headers include crypt.h, which the environment's 2.17 sysroot lacks: give the compiler Debian's
# copy of that one header (vgraph doesn't use crypt; nothing is linked from it).
RUN apt-get update && apt-get install -y --no-install-recommends libcrypt-dev && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/crypt-include && cp /usr/include/crypt.h /tmp/crypt-include/
RUN CPATH=/tmp/crypt-include micromamba run -p /opt/gwas2vcf pip install --no-cache-dir --no-build-isolation /tmp/vgraph.tar.gz \
    && /opt/gwas2vcf/bin/python -c "import pysam, vgraph, marshmallow, Bio, numpy; print('gwas2vcf env ok')"

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git zlib1g libbz2-1.0 liblzma5 libcurl4 libdeflate0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 gwashub
COPY pipeline/images/gwashub/base/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY --from=bcftools /opt/bcftools /opt/bcftools
COPY --from=gwas2vcf /opt/gwas2vcf /opt/gwas2vcf
ENV PATH=/opt/bcftools/bin:$PATH BCFTOOLS_PLUGINS=/opt/bcftools/libexec/bcftools GWAS2VCF_PYTHON=/opt/gwas2vcf/bin/python
RUN bcftools --version | head -1 && bcftools plugin -l | grep -q liftover && /opt/gwas2vcf/bin/python -c "import pysam, vgraph"
WORKDIR /app
COPY pipeline/ pipeline/
ENV PYTHONPATH=/app:/app/pipeline/qc_steps MPLCONFIGDIR=/tmp/mpl HOME=/tmp
USER gwashub
