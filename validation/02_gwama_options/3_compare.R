# Test 2, step 3: compare each site meta-analysis with GWAMA run by hand; write result.json, plot.json, manifest.tsv.
#   Rscript validation/02_gwama_options/3_compare.R
# Needs, for each run (options, sex) in local/validation/02_gwama_options/site/<run>/: meta_results.tsv.gz,
# meta_results_rerun.tsv.gz (the second run), datasets.tsv, and for the options run gwama_gc.txt; reference/ (step 2);
# and site_run.yaml filled in.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("02_gwama_options")
th <- t$cfg$thresholds
site_run <- read_yaml_keep_yn(file.path(t$dir, "site_run.yaml"))
need <- function(...) {
  p <- t$path(...)
  if (!file.exists(p)) stop("Missing ", p, " (see README.md)")
  p
}

checks <- list()
notes <- character()
plot_x <- plot_y <- plot_p <- numeric()
for (run in names(t$cfg$runs)) {
  r <- t$cfg$runs[[run]]
  group <- sprintf("%s run vs GWAMA by hand (%s)", tools::toTitleCase(run), paste(unlist(r$gwama_args)[-(1:2)], collapse = " "))
  site <- read_site_meta(need("site", run, "meta_results.tsv.gz"))
  ref <- read_gwama(need("reference", run, "gwama.out"), r$min_studies)
  A <- align(site, ref)
  a <- A$joined
  checks <- c(checks, gwama_checks(paste0(run, "_"), group, A, th))

  # MAF filter: variants each dataset lost, site (datasets.tsv) vs R; both in the site's input order (by position,
  # as cohort names needn't match the source names)
  ds <- fread(need("site", run, "datasets.tsv"))[order(position)]
  mr <- fread(need("reference", run, "maf_removed.tsv"))
  site_maf <- if ("removed_maf" %in% names(ds)) ds$removed_maf else rep(0L, nrow(ds))
  site_maf[is.na(site_maf)] <- 0L
  checks[[length(checks) + 1]] <- check(paste0(run, "_maf_removed"), "Variants removed by the MAF filter: datasets that differ from R",
                                        if (nrow(ds) == nrow(mr)) sum(site_maf != mr$removed_maf) else nrow(ds), 0, "equal", group)

  if (any(unlist(r$gwama_args) == "--genomic_control")) {
    lam_site <- read_gc(need("site", run, "gwama_gc.txt"))
    lam_ref <- read_gc(need("reference", run, "gwama.gc.out"))
    checks[[length(checks) + 1]] <- check(paste0(run, "_gc_lambda"), "λ per dataset (genomic control): largest absolute difference",
                                          if (length(lam_site) == length(lam_ref)) max(abs(lam_site - lam_ref)) else Inf, th$gc_lambda_max_abs_diff,
                                          group = group)
  }
  if ("sex_diff_p.a" %in% names(a)) {
    checks[[length(checks) + 1]] <- check(paste0(run, "_sex_differentiated_p"), "Sex-differentiated and sex-heterogeneity p: largest relative difference",
                                          max(max_rel_diff(a$sex_diff_p.a, a$sex_diff_p.b), max_rel_diff(a$sex_het_p.a, a$sex_het_p.b)), th$p_max_rel_diff,
                                          group = group)
  }
  rr <- rerun_check(paste0(run, "_rerun"), t$path("site", run, "meta_results.tsv.gz"), t$path("site", run, "meta_results_rerun.tsv.gz"), group)
  if (!is.null(rr)) checks[[length(checks) + 1]] <- rr
  plot_x <- c(plot_x, -log10(a$p.a)); plot_y <- c(plot_y, -log10(a$p.b)); plot_p <- c(plot_p, pmin(a$p.a, a$p.b))
  say("%s: %s variants compared", run, format(nrow(a), big.mark = ","))
  notes <- c(notes, sprintf("%s run: %s variants compared", tools::toTitleCase(run), format(nrow(a), big.mark = ",")))
}

versions <- list(gwama = "2.2.2", r = R.version.string, data.table = as.character(packageVersion("data.table")),
                 site_pipeline_version = site_run$pipeline_version, site_image_digests = site_run$image_digests)
write_result(t, checks, versions, site_run, notes = notes)
write_plot(t, plot_x, plot_y, plot_p, labels = c(x = "GWASHub −log10 p", y = "GWAMA by hand −log10 p"))
write_manifest(t)
