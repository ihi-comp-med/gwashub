# Test 3, step 1: nothing new to upload. The inputs are test 1's QC'd datasets, already on the site; this checks their
# downloads are here and says what to run.
#   Rscript validation/03_metal/1_prepare.R
source(file.path("validation", "_lib", "common.R"))
t <- test_context("03_metal")
qc_dir <- file.path(t$root, "local", "validation", t$cfg$qc_from_test, "site")
for (s in t$cfg$inputs) {
  p <- file.path(qc_dir, paste0(s, "_qc.tsv"))
  if (!file.exists(p)) stop("No ", p, ": run test 1 first (validation/01_gwama_default/README.md)")
  say("%-8s QC'd file: %s", s, p)
}
g <- file.path(t$root, "local", "validation", t$cfg$gwama_from_test, "site", "meta_results.tsv.gz")
if (!file.exists(g)) stop("No ", g, ": test 1's site GWAMA results are needed for the cross-program check")
for (run in names(t$cfg$runs)) {
  p <- t$cfg$runs[[run]]$site_params
  say("Run '%s' on the site with METAL: %s", run, paste(sprintf("%s %s", names(p), unlist(p)), collapse = ", "))
}
