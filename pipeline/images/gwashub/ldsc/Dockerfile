# gwashub/ldsc: LD score regression with the authors' software (bulik/ldsc, Bulik-Sullivan et al. 2015) at a
# pinned commit, in its own Python 2.7 environment (/opt/ldsc), plus the GWASHub runner. Used by gwashub/ldsc.
# The library versions are those of the repository's requirements.txt (numpy 1.16, scipy 0.18, pandas 0.20,
# bitarray 0.8). x86-64 only (image.yaml): Python 2.7 and these releases have no ARM64 builds.
# It doesn't build FROM gwashub/base because that image is ARM64; the last stage repeats it.
#   docker build --platform linux/amd64 -f pipeline/images/gwashub/ldsc/Dockerfile -t gwashub/ldsc .

FROM mambaorg/micromamba:2.0.5 AS ldsc
USER root
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
RUN micromamba create -y -p /opt/ldsc -c conda-forge python=2.7 pip && micromamba clean -afy \
    && /opt/ldsc/bin/pip install --no-cache-dir "numpy==1.16.6" "scipy==0.18.1" "pandas==0.20.3" "bitarray==0.8.3" "nose==1.3.7"
ADD --checksum=sha256:4546d804f89729168f6d1e1dd2c72d3e58a209c9fe2ee8765e271103d1913edb \
    https://github.com/bulik/ldsc/archive/2fdeeb3b44379408794154993dbd6101b8946b7e.tar.gz /tmp/ldsc.tar.gz
RUN mkdir -p /opt/ldsc-src && tar xzf /tmp/ldsc.tar.gz -C /opt/ldsc-src --strip-components=1 \
    && cd /opt/ldsc-src && /opt/ldsc/bin/python ldsc.py -h >/dev/null && /opt/ldsc/bin/python munge_sumstats.py -h >/dev/null

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 gwashub
COPY pipeline/images/gwashub/base/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY --from=ldsc /opt/ldsc /opt/ldsc
COPY --from=ldsc /opt/ldsc-src /opt/ldsc-src
ENV LDSC_PYTHON=/opt/ldsc/bin/python LDSC_DIR=/opt/ldsc-src
WORKDIR /app
COPY pipeline/ pipeline/
ENV PYTHONPATH=/app:/app/pipeline/qc_steps MPLCONFIGDIR=/tmp/mpl HOME=/tmp
USER gwashub
