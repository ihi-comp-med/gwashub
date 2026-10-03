# gwashub/base-r: gwashub/base plus R with data.table and jsonlite, for steps written in R.
FROM gwashub/base
USER root
RUN apt-get update && apt-get install -y --no-install-recommends r-base-core r-cran-data.table r-cran-jsonlite \
    && rm -rf /var/lib/apt/lists/*
USER gwashub
