# Test 2, step 2: GWAMA run by hand, for each run in test.yaml, on the site's QC'd files.
#   Rscript validation/02_gwama_options/2_reference.R
# Needs: the QC'd files the site's meta-analyses used, as local/validation/02_gwama_options/site/<SOURCE>_qc.tsv (for
# UKB, HERMES and FINNGEN, test 1's are used when they aren't there: the same QC runs); and site_run.yaml's
# dataset_order for each run (the site's input order, which sets GWAMA's direction string and its genomic control file).
source(file.path("validation", "_lib", "common.R"))
t <- test_context("02_gwama_options")
site_run <- read_yaml_keep_yn(file.path(t$dir, "site_run.yaml"))

qc_path <- function(s) {
  dirs <- c(t$path("site"), if (!s %in% c("UKB_F", "UKB_M")) file.path(t$root, "local", "validation", t$cfg$qc_from_test, "site"))
  p <- as.vector(outer(dirs, paste0(s, c("_qc.tsv", "_qc.tsv.gz")), file.path))
  p <- p[file.exists(p)]
  if (!length(p)) stop("Download the QC'd file for ", s, " to ", t$path("site", paste0(s, "_qc.tsv")))
  say("%s: %s", s, p[1])
  p[1]
}

only <- commandArgs(trailingOnly = TRUE)  # optional: the runs to do (default: all)
for (run in if (length(only)) only else names(t$cfg$runs)) {
  r <- t$cfg$runs[[run]]
  dir.create(t$path("reference", run), recursive = TRUE, showWarnings = FALSE)
  order <- unlist(site_run$runs[[run]]$dataset_order %||% r$datasets)
  if (!setequal(order, r$datasets)) stop("site_run.yaml runs.", run, ".dataset_order must list: ", paste(r$datasets, collapse = ", "))
  loaded <- setNames(lapply(order, function(s) load_qc(qc_path(s), r$maf_min)), order)
  tables <- lapply(loaded, `[[`, "d")
  fwrite(data.table(dataset = order, removed_maf = vapply(loaded, `[[`, integer(1), "removed_maf")),
         t$path("reference", run, "maf_removed.tsv"), sep = "\t")
  sexes <- if (any(unlist(r$gwama_args) == "--sex")) vapply(order, function(s) substr(t$sources[[s]]$sex, 1, 1), "") else NULL
  out <- gwama_by_hand(t$path("reference", run), tables, r$gwama_args, t$cfg$reference$gwama_image, sexes)
  say("%s: GWAMA by hand -> %s", run, out)
}
