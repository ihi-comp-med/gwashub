# Where this file comes from

`qc_munge.R` is the Skin Genetics Consortium's own QC script, copied from their repository with **two minor edits**,
listed below; nothing else differs. Don't change it here beyond such fixes: change it in SGC's repository, then copy
it in again, re-apply any edit SGC hasn't taken up, and update this file. `pipeline/tests/test_sgc.py` checks that
the file matches the checksum "here" below.

| File | Repository | Commit | sha256 (SGC's) | sha256 (here) |
|---|---|---|---|---|
| `qc_munge.R` | https://github.com/Katie-Watts/SGC | `1bd3df946afbc8b34d0888aa35acad4b3ac6ada5` | `5a68ada6281990df102ef3e369150bb531e0e6b7586be5364ad919429c95d1d5` | `2ec9acf261549446d8f5a3de10974df0fcc02ea63aa96002e3887556e9f1c7e7` |

## GWASHub edits

Each edited line is marked `# GWASHub edit N` in the script. Both are worth making in SGC's repository too.

1. **The header map is loaded with `data()`.** `MungeSumstats::sumstatsColHeaders` fails with current MungeSumstats
   (1.18.1, Bioconductor 3.22: "'sumstatsColHeaders' is not an exported object"): the package ships it as a dataset
   without LazyData, so it can only be loaded with `data()`. Same table, loaded the way the package allows.
2. **A `--chain FILE` option**, passed to `format_sumstats(local_chain = ...)`. Without it the script downloads the
   GRCh37-to-GRCh38 chain file as before. GWASHub steps don't use the network, and MungeSumstats' own offline
   fallback (the Ensembl chain file it ships) fails when the download can't connect at all ("No such file:
   .../GRCh37_to_GRCh38.chain.gz"), so GWASHub passes that shipped Ensembl file here: the chain SGC's runs download
   by default (`chain_source = "ensembl"`), in MungeSumstats' copy of 2022-11-25.

The whole difference from SGC's file (`diff <SGC's qc_munge.R> qc_munge.R`):

```
35a36
> #   --chain FILE        local liftover chain file; default: download one (GWASHub edit 2, see SOURCE.md)
68a70
> CHAIN   <- get_opt("--chain", default = NULL)   # GWASHub edit 2 (SOURCE.md): local chain file for the liftover
97c99,101
< mapping_file <- MungeSumstats::sumstatsColHeaders
---
> # GWASHub edit 1 (SOURCE.md): MungeSumstats::sumstatsColHeaders isn't exported (no LazyData); load it with data().
> utils::data("sumstatsColHeaders", package = "MungeSumstats", envir = environment())
> mapping_file <- sumstatsColHeaders
189a194
>       local_chain          = CHAIN,            # GWASHub edit 2 (SOURCE.md); NULL = download as before
```

Everything else in this folder is GWASHub code around it: `gwashub_qc_munge.py` (writes the dataset as a cohort file
for `qc_munge.R`, runs it and reads its output back), `step.yaml` and the test. SGC's `qc_setup.R`, which installs
MungeSumstats and its references, is with the image it builds: `pipeline/images/sgc/mungesumstats/`.

To update: copy the file from the commit you want (e.g. `gh api "repos/Katie-Watts/SGC/contents/qc_munge.R?ref=<commit>"
-H "Accept: application/vnd.github.raw" > qc_munge.R`), re-apply the edits above that SGC hasn't made, record the commit
and both checksums (`shasum -a 256 qc_munge.R`, before and after), and run the tests.
