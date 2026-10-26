# sgc/mungesumstats: gwashub/base-r plus MungeSumstats and its references, installed by the Skin Genetics Consortium's
# own setup script (qc_setup.R in this folder, copied unchanged from github.com/Katie-Watts/SGC; see SOURCE.md):
# MungeSumstats, data.table, SNPlocs.Hsapiens.dbSNP155.GRCh38 and .GRCh37, BSgenome.Hsapiens.NCBI.GRCh38 and
# BSgenome.Hsapiens.1000genomes.hs37d5 (several GB). For the sgc/qc_munge and sgc/check_build steps.
#
# The R packages are built in their own stage, which depends only on qc_setup.R: GWASHub code changes rebuild
# gwashub/base-r, and this way they don't reinstall the packages (hours), only copy them in.
#   docker build -f pipeline/images/sgc/mungesumstats/Dockerfile -t sgc/mungesumstats .
# Pinned by digest: a new python:3.12-slim on Docker Hub would otherwise rebuild this stage (about 40 minutes, then a
# 16 GB upload). Update the digest deliberately, with gwashub/base's Debian in mind (the same R must result).
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS rlib
# The same R as gwashub/base-r (Debian's r-base-core), plus what Bioconductor packages need to compile.
RUN apt-get update && apt-get install -y --no-install-recommends r-base-core r-base-dev r-cran-data.table \
        libcurl4-openssl-dev libssl-dev libxml2-dev zlib1g-dev libbz2-dev liblzma-dev libdeflate-dev libpng-dev \
        libfontconfig1-dev libfreetype-dev libharfbuzz-dev libfribidi-dev libtiff-dev libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*
# R gives each download 5 minutes by default; dbSNP 155 for GRCh38 alone is 5.9 GB. Set here, not in SGC's script.
ENV R_LIBS_SITE=/opt/sgc/R R_DEFAULT_INTERNET_TIMEOUT=7200
RUN mkdir -p /opt/sgc/R
# One layer per large package, each installed as qc_setup.R installs it (BiocManager::install), so a dropped download
# repeats only its own layer and the rest stay cached. qc_setup.R then finds them installed ("ok") and skips them.
# install() tries a package up to three times: Bioconductor's 6 GB downloads sometimes break off.
RUN echo 'install <- function(p) { .libPaths(c("/opt/sgc/R", .libPaths())); options(timeout = 7200); \
      if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager", repos = "https://cloud.r-project.org"); \
      for (i in 1:3) { if (requireNamespace(p, quietly = TRUE)) return(invisible()); \
        BiocManager::install(p, update = FALSE, ask = FALSE) }; stop(p, " did not install") }' > /opt/sgc/install.R
RUN Rscript -e 'source("/opt/sgc/install.R"); install("MungeSumstats")'
RUN Rscript -e 'source("/opt/sgc/install.R"); install("SNPlocs.Hsapiens.dbSNP155.GRCh38")'
RUN Rscript -e 'source("/opt/sgc/install.R"); install("SNPlocs.Hsapiens.dbSNP155.GRCh37")'
RUN Rscript -e 'source("/opt/sgc/install.R"); install("BSgenome.Hsapiens.NCBI.GRCh38")'
RUN Rscript -e 'source("/opt/sgc/install.R"); install("BSgenome.Hsapiens.1000genomes.hs37d5")'
COPY pipeline/images/sgc/mungesumstats/qc_setup.R /opt/sgc/qc_setup.R
# SGC's setup, as their README says to run it: it checks every package it needs and installs any still missing (it
# exits non-zero, failing the build, if one won't install).
RUN Rscript -e '.libPaths(c("/opt/sgc/R", .libPaths())); options(timeout = 7200); source("/opt/sgc/qc_setup.R")' \
    && Rscript -e '.libPaths(c("/opt/sgc/R", .libPaths())); ip <- installed.packages(); \
         p <- c("MungeSumstats", "data.table", "SNPlocs.Hsapiens.dbSNP155.GRCh38", "SNPlocs.Hsapiens.dbSNP155.GRCh37", \
                "BSgenome.Hsapiens.NCBI.GRCh38", "BSgenome.Hsapiens.1000genomes.hs37d5", "BiocManager", "rtracklayer"); \
         v <- data.frame(package = p, version = ip[p, "Version"]); \
         v <- rbind(data.frame(package = c("R", "Bioconductor"), version = c(as.character(getRversion()), \
                    as.character(BiocManager::version()))), v); \
         write.table(v, "/opt/sgc/versions.tsv", sep = "\t", quote = FALSE, row.names = FALSE); print(v)'

FROM gwashub/base-r
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libcurl4 libssl3 libxml2 libdeflate0 libpng16-16 \
        libfontconfig1 libfreetype6 libharfbuzz0b libfribidi0 libtiff6 libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/*
COPY --from=rlib /opt/sgc /opt/sgc
ENV R_LIBS_SITE=/opt/sgc/R
RUN Rscript -e 'suppressMessages(library(MungeSumstats)); cat("MungeSumstats", as.character(packageVersion("MungeSumstats")), "\n")'
USER gwashub
