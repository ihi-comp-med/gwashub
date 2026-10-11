# Test 1, step 3: compare the site's meta-analysis with GWAMA run by hand; write result.json, plot.json, manifest.tsv.
#   Rscript validation/01_gwama_default/3_compare.R
# Needs: site/meta_results.tsv.gz (and site/meta_results_rerun.tsv.gz from the second run), reference/ (step 2),
# and site_run.yaml filled in.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("01_gwama_default")
th <- t$cfg$thresholds

site <- fread(t$path("site", "meta_results.tsv.gz"), colClasses = list(character = "chr"))
g <- fread(t$path("reference", "gwama.out"))[n_studies >= t$cfg$reference$min_studies]
g[, c("chr", "pos") := tstrsplit(rs_number, ":", keep = 1:2)][, pos := as.integer(pos)]
gwama <- g[, .(chr, pos, ea = reference_allele, oa = other_allele, beta, se, p = `p-value`, n_studies,
               direction = effects, q = q_statistic, het_p = `q_p-value`, i2, n = n_samples,
               eaf = fifelse(eaf >= 0, eaf, NA_real_))]

# The direction string has one symbol per dataset in input order; GWAMA by hand used test.yaml's order, the site its own
# (site_run.yaml dataset_order, from the meta-analysis's datasets.tsv), so put the by-hand strings in the site's order.
site_run <- read_yaml_keep_yn(file.path(t$dir, "site_run.yaml"))
order <- unlist(site_run$dataset_order %||% t$cfg$inputs)
if (!setequal(order, t$cfg$inputs)) stop("site_run.yaml dataset_order must list the inputs: ", paste(t$cfg$inputs, collapse = ", "))
idx <- match(order, t$cfg$inputs)
gwama[, direction := vapply(strsplit(direction, ""), function(v) paste(v[idx], collapse = ""), "")]

A <- align(site, gwama)
a <- A$joined

checks <- list(
  check("variant_sets", "Same variants as GWAMA by hand (variants only in one)",
        length(A$only_a) + length(A$only_b), 0, "equal"),
  check("beta_se", "β and se vs GWAMA by hand: largest absolute difference",
        max(max_abs_diff(a$beta.a, a$beta.b), max_abs_diff(a$se.a, a$se.b)), th$beta_se_max_abs_diff),
  check("p", "p vs GWAMA by hand: largest relative difference", max_rel_diff(a$p.a, a$p.b), th$p_max_rel_diff),
  check("n_studies_direction", "n_studies and direction vs GWAMA by hand: rows that differ",
        sum(a$n_studies.a != a$n_studies.b | a$direction.a != a$direction.b), 0, "equal"),
  check("q_i2", "Heterogeneity (Q, its p and I²) vs GWAMA by hand: largest absolute difference",
        max(max_abs_diff(a$q.a, a$q.b), max_abs_diff(a$het_p.a, a$het_p.b), max_abs_diff(a$i2.a, a$i2.b)), th$het_max_abs_diff),
  check("n_eaf", "Sample size and EAF vs GWAMA by hand: largest absolute difference",
        max(max_abs_diff(a$n.a, a$n.b), max_abs_diff(a$eaf.a, a$eaf.b)), th$n_eaf_max_abs_diff)
)
rerun <- t$path("site", "meta_results_rerun.tsv.gz")
if (file.exists(rerun)) {
  checks[[length(checks) + 1]] <- check("rerun", "Second site run byte-identical (0 = same sha256)",
                                        as.integer(sha256(t$path("site", "meta_results.tsv.gz")) != sha256(rerun)), 0, "equal")
} else {
  say("No %s: the re-run check is missing (run the meta-analysis a second time)", rerun)
}

versions <- list(gwama = "2.2.2", r = R.version.string, data.table = as.character(packageVersion("data.table")),
                 site_pipeline_version = site_run$pipeline_version, site_image_digests = site_run$image_digests)
notes <- sprintf("%s variants compared; %d had the effect allele the other way round in GWAMA's output",
                 format(nrow(a), big.mark = ","), A$flipped)
write_result(t, checks, versions, site_run, notes = notes)
write_plot(t, -log10(a$p.a), -log10(a$p.b), pmin(a$p.a, a$p.b),
           labels = c(x = "GWASHub −log10 p", y = "GWAMA by hand −log10 p"))
write_manifest(t)
