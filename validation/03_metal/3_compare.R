# Test 3, step 3: site METAL vs METAL by hand (both runs), site METAL vs site GWAMA (test 1), and the two METAL schemes
# against each other; write result.json, plot.json, manifest.tsv.
#   Rscript validation/03_metal/3_compare.R
# Needs, for each run (stderr, samplesize) in local/validation/03_metal/site/<run>/: meta_results.tsv.gz,
# meta_results_rerun.tsv.gz and lead_loci.tsv; reference/ (step 2); test 1's site/meta_results.tsv.gz; site_run.yaml.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("03_metal")
th <- t$cfg$thresholds
site_run <- read_yaml_keep_yn(file.path(t$dir, "site_run.yaml"))
need <- function(p) {
  if (!file.exists(p)) stop("Missing ", p, " (see README.md)")
  p
}

# METAL's table in the site's result columns (as the site maps it; n_studies = HetDf + 1).
read_metal <- function(path, min_studies) {
  m <- fread(path)
  parts <- tstrsplit(m$MarkerName, ":", fixed = TRUE)
  out <- data.table(chr = parts[[1]], pos = as.integer(parts[[2]]), ea = toupper(m$Allele1), oa = toupper(m$Allele2),
                    p = m$`P-value`, direction = gsub("0", "?", m$Direction), n_studies = m$HetDf + 1, q = m$HetChiSq,
                    het_p = m$HetPVal, i2 = m$HetISq / 100)
  if ("Effect" %in% names(m)) out[, `:=`(beta = m$Effect, se = m$StdErr)] else out[, `:=`(z = m$Zscore, n = m$Weight)]
  if ("Freq1" %in% names(m)) out[, eaf := m$Freq1]
  if ("MinFreq" %in% names(m)) out[, `:=`(eaf_min = m$MinFreq, eaf_max = m$MaxFreq)]
  out[n_studies >= min_studies]
}

checks <- list()
add <- function(...) checks[[length(checks) + 1]] <<- check(...)
site <- list()
for (run in names(t$cfg$runs)) {
  p <- t$cfg$runs[[run]]$site_params
  s <- read_site_meta(need(t$path("site", run, "meta_results.tsv.gz")))
  site[[run]] <- s
  ref <- read_metal(need(t$path("reference", run, "metal1.tbl")), p$min_studies)
  A <- align(s, ref)
  a <- A$joined
  # align() flips β, Z and EAF where the effect alleles differ; min/max EAF swap and flip too
  flip <- a$ea.a != a$ea.b
  if ("eaf_min.b" %in% names(a)) a[flip, c("eaf_min.b", "eaf_max.b") := .(1 - eaf_max.b, 1 - eaf_min.b)]
  RUN_NAMES <- c(stderr = "Standard-error scheme", samplesize_plain = "Sample-size scheme", samplesize = "Sample-size scheme, options on")
  group <- sprintf("%s vs METAL by hand", RUN_NAMES[[run]] %||% run)
  add(paste0(run, "_variant_sets"), "Same variants, incl. MINWEIGHT removals (variants only in one)", length(A$only_a) + length(A$only_b), 0, "equal", group)
  num <- intersect(c("beta", "se", "z", "n", "eaf", "eaf_min", "eaf_max", "q", "i2"), sub("\\.a$", "", grep("\\.a$", names(a), value = TRUE)))
  num <- num[paste0(num, ".b") %in% names(a)]
  add(paste0(run, "_values"), sprintf("%s: largest absolute difference", paste(num, collapse = ", ")),
      max(vapply(num, function(c) max_abs_diff(a[[paste0(c, ".a")]], a[[paste0(c, ".b")]]), numeric(1))), th$metal_max_abs_diff, group = group)
  add(paste0(run, "_p"), "p and heterogeneity p: largest relative difference",
      max(max_rel_diff(a$p.a, a$p.b), max_rel_diff(a$het_p.a, a$het_p.b)), th$p_max_rel_diff, group = group)
  add(paste0(run, "_direction"), "n_studies and direction: rows that differ",
      sum(a$n_studies.a != a$n_studies.b | a$direction.a != a$direction.b), 0, "equal", group)
  rr <- rerun_check(paste0(run, "_rerun"), t$path("site", run, "meta_results.tsv.gz"), t$path("site", run, "meta_results_rerun.tsv.gz"), group)
  if (!is.null(rr)) checks[[length(checks) + 1]] <- rr
  say("%s: %s variants compared with METAL by hand", run, format(nrow(a), big.mark = ","))
}

# Cross-program: METAL stderr vs GWAMA fixed effects (test 1's site run), both through the site.
gwama <- read_site_meta(need(file.path(t$root, "local", "validation", t$cfg$gwama_from_test, "site", "meta_results.tsv.gz")))
X <- align(site$stderr, gwama)$joined
XG <- "METAL (standard error) vs GWAMA (fixed effects), both on GWASHub"
add("cross_program_beta_se", "β and se: largest absolute difference",
    max(max_abs_diff(X$beta.a, X$beta.b), max_abs_diff(X$se.a, X$se.b)), th$cross_beta_se_max_abs_diff, group = XG)
Xp <- X[p.b > th$cross_p_above]
add("cross_program_p", sprintf("−log10 p for p > %g: largest absolute difference", th$cross_p_above),
    neglog10_diff(Xp$p.a, Xp$p.b), th$cross_neglog10p_max_abs_diff, group = XG)
# How far each program's p is from the exact normal p of its own β/se, over all variants (reported, not judged).
exact <- 2 * pnorm(-abs(X$beta.a / X$se.a))
tail_note <- sprintf("p vs the exact normal p of β/se, largest −log10 difference over all variants: METAL %.4f, GWAMA %.4f (GWAMA's p drifts in the far tail)",
                     neglog10_diff(X$p.a, exact), neglog10_diff(X$p.b, exact))

# Cross-scheme: samplesize vs stderr.
S <- align(site$samplesize_plain, site$stderr)$joined
r <- cor(-log10(S$p.a), -log10(S$p.b))
SG <- "METAL sample-size scheme vs standard-error scheme"
add("cross_scheme_cor", "1 − correlation of −log10 p", 1 - r, 1 - th$cross_scheme_neglog10p_min_cor, group = SG)
leads <- lapply(c("stderr", "samplesize_plain"), function(run) fread(need(t$path("site", run, "lead_loci.tsv")), colClasses = list(character = "chr")))
w <- th$lead_locus_window_kb * 1000
unmatched <- function(x, y) x[vapply(seq_len(nrow(x)), function(i) !any(y$chr == x$chr[i] & abs(y$pos - x$pos[i]) <= w), logical(1))]
lone <- rbind(unmatched(leads[[1]], leads[[2]])[, .(run = "stderr", chr, pos, p)],
              unmatched(leads[[2]], leads[[1]])[, .(run = "samplesize_plain", chr, pos, p)])
add("cross_scheme_lead_loci", sprintf("Lead loci with p < %g in one and none within %d kb in the other",
                                      th$lead_locus_firm_p, th$lead_locus_window_kb), lone[p < th$lead_locus_firm_p, .N], 0, "equal", group = SG)
lone_note <- if (nrow(lone)) sprintf("Lead loci in one scheme only (all near the threshold): %s",
                                     paste(sprintf("%s %s:%s p=%.2g", lone$run, lone$chr, lone$pos, lone$p), collapse = "; ")) else character()

versions <- list(metal = "2020-05-05", r = R.version.string, data.table = as.character(packageVersion("data.table")),
                 site_pipeline_version = site_run$pipeline_version, site_image_digests = site_run$image_digests)
write_result(t, checks, versions, site_run,
             notes = c(sprintf("Cross-program: %s variants in both METAL stderr and GWAMA (test 1's GWAMA run used QC runs whose files are numerically identical to these)", format(nrow(X), big.mark = ",")), tail_note,
                       sprintf("Cross-scheme: %s variants; −log10 p correlation %.5f; lead loci %d (stderr) and %d (samplesize, plain)",
                               format(nrow(S), big.mark = ","), r, nrow(leads[[1]]), nrow(leads[[2]])), lone_note))
write_plot(t, -log10(X$p.a), -log10(X$p.b), pmin(X$p.a, X$p.b), labels = c(x = "METAL stderr (GWASHub) −log10 p", y = "GWAMA fixed (GWASHub) −log10 p"))
write_manifest(t)
