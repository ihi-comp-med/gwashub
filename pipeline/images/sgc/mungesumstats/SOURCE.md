# Where this file comes from

`qc_setup.R` is the Skin Genetics Consortium's own setup script, copied **unchanged** (byte for byte) from their
repository. The Dockerfile runs it, as SGC's README says to set a machine up, to install MungeSumstats and its
references (dbSNP 155 and the GRCh37 and GRCh38 genomes) into the image. Don't edit it here: change it in SGC's
repository, then copy it in again and update this file. `pipeline/tests/test_sgc.py` checks that the copy still matches
the checksum below (unchanged: both checksums are the same).

| File | Repository | Commit | sha256 (SGC's) | sha256 (here) |
|---|---|---|---|---|
| `qc_setup.R` | https://github.com/Katie-Watts/SGC | `1bd3df946afbc8b34d0888aa35acad4b3ac6ada5` | `8de874b93f6c81c60d9209212a6bc553fc8327fd1aaa598dbd5857e625b7f529` | `8de874b93f6c81c60d9209212a6bc553fc8327fd1aaa598dbd5857e625b7f529` |

The Dockerfile sets two things around it, not in it: R's download time limit (R_DEFAULT_INTERNET_TIMEOUT, 2 hours;
the default 5 minutes isn't enough for dbSNP 155, 5.9 GB for GRCh38) and the library it installs into (/opt/sgc/R).
