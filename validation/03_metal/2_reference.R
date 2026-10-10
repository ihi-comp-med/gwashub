# Test 3, step 2: METAL run by hand, for each run in test.yaml, on the QC'd files the site's meta-analyses used.
#   Rscript validation/03_metal/2_reference.R
# Needs: the QC'd files the site's meta-analyses used, as local/validation/03_metal/site/<SOURCE>_qc.tsv (test 1's
# are used when they aren't there: the same QC runs), and site_run.yaml's dataset_order (the site's input order, which
# sets METAL's direction string). METAL runs in the site's own METAL image (same release); its inputs and
# script are written here, not by the site.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("03_metal")
site_run <- read_yaml_keep_yn(file.path(t$dir, "site_run.yaml"))
qc_path <- function(s) {
  p <- c(t$path("site", paste0(s, "_qc.tsv")), file.path(t$root, "local", "validation", t$cfg$qc_from_test, "site", paste0(s, "_qc.tsv")))
  p <- p[file.exists(p)]
  if (!length(p)) stop("Download the QC'd file for ", s, " to ", t$path("site", paste0(s, "_qc.tsv")))
  say("%s: %s", s, p[1])
  p[1]
}

order <- unlist(site_run$dataset_order %||% t$cfg$inputs)
if (!setequal(order, t$cfg$inputs)) stop("site_run.yaml dataset_order must list: ", paste(t$cfg$inputs, collapse = ", "))
for (run in names(t$cfg$runs)) {
  p <- t$cfg$runs[[run]]$site_params
  dir <- t$path("reference", run)
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  files <- character()
  for (s in order) {
    d <- load_qc(qc_path(s), p$maf_min)$d
    f <- paste0(s, ".metal.tsv")
    fwrite(d[, .(MARKERNAME = m, EA = ea, NEA = oa, BETA = beta, SE = se, P = p, N = n, EAF = eaf)],
           file.path(dir, f), sep = "\t", na = "NA", quote = FALSE)
    files <- c(files, f)
  }
  onoff <- function(x) if (isTRUE(x)) "ON" else "OFF"
  script <- c(paste("SCHEME", toupper(p$scheme)), "AVERAGEFREQ ON", paste("MINMAXFREQ", onoff(p$minmax_freq)),
              paste("GENOMICCONTROL", onoff(p$genomic_control)), paste("OVERLAP", onoff(p$overlap)), paste("ZCUTOFF", p$zcutoff),
              paste("MINWEIGHT", p$min_weight), paste("EFFECT_PRINT_PRECISION", p$precision), paste("STDERR_PRINT_PRECISION", p$precision),
              "MARKER MARKERNAME", "ALLELE EA NEA", "EFFECT BETA", "STDERR SE", "PVALUE P", "WEIGHT N", "FREQ EAF",
              "COLUMNCOUNTING LENIENT", paste("PROCESS", files), "OUTFILE metal .tbl", "ANALYZE HETEROGENEITY", "QUIT")
  writeLines(script, file.path(dir, "metal_script.txt"))
  a <- c("run", "--rm", "-v", paste0(dir, ":/w"), "-w", "/w", t$cfg$reference$metal_image, "metal", "metal_script.txt")
  say("$ docker %s", paste(a, collapse = " "))
  if (system2("docker", a, stdout = file.path(dir, "metal_console.txt"), stderr = file.path(dir, "metal_console.txt")) != 0 ||
      !file.exists(file.path(dir, "metal1.tbl"))) stop("METAL failed; see ", file.path(dir, "metal_console.txt"))
  say("%s: METAL by hand -> %s", run, file.path(dir, "metal1.tbl"))
}
