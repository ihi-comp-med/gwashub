# gwashub/gwama: gwashub/base plus GWAMA v2.2.2 (Mägi & Morris 2010, Estonian Genome Centre), for the
# gwashub/gwama meta-analysis method. The source is pinned by checksum.
FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ make unzip curl ca-certificates zlib1g-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o gwama.zip https://www.geenivaramu.ee/tools/GWAMA_v2.2.2.zip \
    && echo "f0e1f9d4bb739aa344ee7215c76d79ea22302bc18f7f318598ca95486f89ae8a  gwama.zip" | sha256sum -c - \
    && unzip -q gwama.zip -x "__MACOSX/*" \
    && g++ -O2 -fpermissive -w -Wno-deprecated main.cpp marker.cpp statistics.cpp study.cpp chisquaredistr.cpp normaldistr.cpp \
           gammaf.cpp igammaf.cpp ap.cpp global.cpp problem.cpp tools.cpp cohort.cpp commandLine.cpp readFile.cpp -lz -o GWAMA

FROM gwashub/base
COPY --from=build /src/GWAMA /usr/local/bin/GWAMA
