# Reference resources

The reference files GWASHub's QC steps and meta-analyses read (genomes, chain, dbSNP, 1000 Genomes, LD scores):
where each comes from, and the script that builds it. Only the scripts and this record are in git; the files
themselves (tens of GB) are built locally and uploaded to GWASHub as shared resources.

| | |
|---|---|
| `catalog.yaml` | Every resource: its keys (the file names steps use), source, version, licence, which steps use it, its build script |
| `build/*.sh` | One script per source: download (with the published checksum where there is one), transform, write the GWASHub files |
| `build/_common.sh` | Shared helpers; tools run in GWASHub's own images (bcftools, pysam: `gwashub/vcf`; PLINK 2: `gwashub/base`) |

A test (`pipeline/tests/test_resources_catalog.py`) fails if a step declares a resource key that isn't in the
catalogue, so a new step can't need a file without its provenance written down here.

## Building

```bash
./devtools/build_images.sh gwashub/base gwashub/vcf       # the tools the scripts use
./resources/build/chain.sh                                 # each script is independent; run the ones you need
./resources/build/w_hm3.sh
./resources/build/ldsc_panukb.sh                           # 2.4 GB download
./resources/build/fasta_grch37.sh                          # 0.9 GB
./resources/build/fasta_grch38.sh                          # 3.2 GB
./resources/build/kg1000_b38.sh                            # ~4 GB; 1000G panels + frequencies
./resources/build/genes_gencode.sh                         # 80 MB; genes for locus plots
./resources/build/dbsnp157_b38.sh                          # ~30 GB; several hours
```

Files go to `local/resources/` (git-ignored; set `GWASHUB_RESOURCES_OUT` to put them elsewhere) and downloads to
`local/resources/_downloads/`, which you can delete afterwards. Downloads resume if interrupted. Each script appends
what it built (key, sha256, size, script, date) to `local/resources/BUILT.tsv`.

## Uploading

A platform admin uploads each file as a **shared** resource (Pipeline settings → Resources → Add resource), with the
key exactly as in the catalogue, and the title, build and source from `catalog.yaml`. Pipelines pin resource versions,
so uploading a new version of a file doesn't change runs already made.

## Status

| Resource | Script | Built and uploaded | To confirm |
|---|---|---|---|
| Chain GRCh37 → GRCh38 | `chain.sh` | | |
| GRCh37 FASTA | `fasta_grch37.sh` | | |
| GRCh38 FASTA (no "chr") | `fasta_grch38.sh` | | same contigs as the HERMES 3 HPC file (compare `.fai`) |
| dbSNP 157 (GRCh38) | `dbsnp157_b38.sh` | | same "clean" steps as the HERMES 3 HPC file |
| 1000G frequencies (GRCh38) | `kg1000_b38.sh` | | HERMES 3 used phase 3 lifted to GRCh38; this is the 2022 high-coverage callset |
| 1000G LD panels (GRCh38) | `kg1000_b38.sh` | | used once lead-locus clumping is added |
| GENCODE 50 genes (GRCh38) | `genes_gencode.sh` | | |
| Pan-UKB LD scores | `ldsc_panukb.sh` | | |
| HapMap 3 SNP list | `w_hm3.sh` | | |

Locally (`./devtools/dev.sh`), synthetic stand-ins under the same keys come from `devtools/synth.py`; nothing here is
needed to develop or test.
