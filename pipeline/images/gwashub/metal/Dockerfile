# gwashub/metal: gwashub/base plus METAL 2020-05-05 (Willer, Li & Abecasis 2010; statgen/METAL), for the
# gwashub/metal meta-analysis method. The source is pinned by checksum.
FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ make cmake zlib1g-dev curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o metal.tar.gz https://github.com/statgen/METAL/archive/refs/tags/2020-05-05.tar.gz \
    && echo "0ffa2419ca2ab43766e7e6e8c97822c8ce1f5b6233fb5f992d1b1be1955fede7  metal.tar.gz" | sha256sum -c - \
    && tar xzf metal.tar.gz && mkdir build && cd build \
    && cmake -DCMAKE_BUILD_TYPE=Release ../METAL-2020-05-05 && make -j4 \
    && cp metal/metal /src/metal-bin

FROM gwashub/base
COPY --from=build /src/metal-bin /usr/local/bin/metal
