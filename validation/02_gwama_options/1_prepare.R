# Test 2, step 1: the female and male files to upload (UKB, HERMES and FINNGEN are already on the site from test 1).
#   Rscript validation/02_gwama_options/1_prepare.R
# Needs: test 1's prepared inputs (Rscript validation/01_gwama_default/1_prepare.R).
# Output: local/validation/02_gwama_options/inputs/UKB_F.tsv and UKB_M.tsv (standard column names), and the upload
# details to type into the site.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("02_gwama_options")

same <- file.path(t$root, "local", "validation", t$cfg$recipe$same_variants_as)
if (!file.exists(same)) stop("No ", same, ": run Rscript validation/01_gwama_default/1_prepare.R first")
keep <- fread(same, select = "rsid")$rsid

for (s in c("UKB_F", "UKB_M")) {
  d <- read_source(t, s)
  d <- d[rsid %in% keep & chr %in% as.character(1:22)]
  d <- unique(d, by = "rsid")[order(as.integer(chr), pos)]
  d <- d[, intersect(STANDARD, names(d)), with = FALSE]
  path <- t$path("inputs", paste0(s, ".tsv"))
  fwrite(d, path, sep = "\t", na = "NA", quote = FALSE)
  src <- t$sources[[s]]
  say("%-6s %s of test 1's %s variants -> %s", s, format(nrow(d), big.mark = ","), format(length(keep), big.mark = ","), path)
  say("       upload as: cohort %s, build %s, ancestry %s, sex %s, cases %s, controls %s", s, src$build, src$ancestry,
      src$sex, src$n_cases, src$n_controls)
}
say("Next: upload these to the '%s' project and run QC (see README.md).", t$cfg$site$project)
