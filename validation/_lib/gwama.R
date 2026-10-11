# GWAMA run by hand, and reading meta-analysis results, for tests that compare the site's meta-analyses with the
# programs run directly (tests 2 and 3). Inputs are prepared here in R from the site's QC'd files, independently of
# the site's own code (pipeline/analyses/meta_analysis/_metalib).

# A QC'd file as the site loads it before a meta-analysis: rows without BETA/SE or with SE <= 0 dropped, then (when
# maf_min > 0) MAF below maf_min dropped (a missing EAF is kept); one row per marker (the first). Returns the rows and
# how many the MAF filter removed.
load_qc <- function(path, maf_min = 0) {
  d <- fread(path, colClasses = list(character = c("chr", "ea", "oa")))
  d <- d[!is.na(beta) & !is.na(se) & se > 0]
  removed_maf <- 0L
  if (maf_min > 0) {
    keep <- is.na(d$eaf) | pmin(d$eaf, 1 - d$eaf) >= maf_min
    removed_maf <- sum(!keep)
    d <- d[keep]
  }
  d[, m := marker_id(chr, pos, ea, oa)]
  list(d = unique(d, by = "m"), removed_maf = removed_maf)
}

# Run GWAMA (the site's image, so the same release) on named QC'd tables in the given order, in dir. sexes: "M"/"F" per
# input for --sex. Returns the path of gwama.out.
gwama_by_hand <- function(dir, tables, args, image = "gwashub/gwama", sexes = NULL) {
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  for (s in names(tables)) {
    d <- tables[[s]]
    fwrite(d[, .(MARKERNAME = m, EA = ea, NEA = oa, BETA = beta, SE = se, N = round(n), EAF = eaf)],
           file.path(dir, paste0(s, ".gwama.tsv")), sep = "\t", na = "NA", quote = FALSE)
  }
  lines <- paste0(names(tables), ".gwama.tsv")
  if (!is.null(sexes)) lines <- paste(lines, sexes, sep = "\t")
  writeLines(lines, file.path(dir, "gwama.in"))
  a <- c("run", "--rm", "-v", paste0(dir, ":/w"), "-w", "/w", image, "GWAMA", "-i", "gwama.in", "-o", "gwama", unlist(args))
  say("$ docker %s", paste(a, collapse = " "))
  if (system2("docker", a) != 0) stop("GWAMA failed; see ", file.path(dir, "gwama.log.out"))
  file.path(dir, "gwama.out")
}

# GWAMA's output in the site's result columns (chr pos ea oa beta se p n_studies direction q het_p i2 n eaf, and the
# sex-differentiated p-values when there).
read_gwama <- function(path, min_studies) {
  g <- fread(path)[n_studies >= min_studies]
  g[, c("chr", "pos") := tstrsplit(rs_number, ":", keep = 1:2)][, pos := as.integer(pos)]
  out <- g[, .(chr, pos, ea = reference_allele, oa = other_allele, beta, se, p = `p-value`, n_studies,
               direction = effects, q = q_statistic, het_p = `q_p-value`, i2, n = n_samples,
               eaf = fifelse(eaf >= 0, eaf, NA_real_))]
  if ("gender_differentiated_p-value" %in% names(g))
    out[, `:=`(sex_diff_p = g$`gender_differentiated_p-value`, sex_het_p = g$`gender_heterogeneity_p-value`)]
  out
}

# λ per dataset from a GWAMA genomic-control file (a header, then one line per input: the file and its λ), in input order.
read_gc <- function(path) {
  x <- strsplit(trimws(readLines(path)[-1]), "[\t ]+")
  as.numeric(vapply(x, `[`, "", 2))
}

# The site's meta-analysis results, with the sex-differentiated columns renamed as read_gwama names them.
read_site_meta <- function(path) {
  s <- fread(path, colClasses = list(character = "chr"))
  old <- intersect(c("gender_differentiated_p", "gender_heterogeneity_p"), names(s))
  setnames(s, old, sub("gender_differentiated_p", "sex_diff_p", sub("gender_heterogeneity_p", "sex_het_p", old)))
  s
}

# The checks every GWAMA comparison makes (as test 1): variants, β/se, p, n_studies and direction, heterogeneity,
# N and EAF. A = align(site, reference); group: the heading they show under (e.g. "Options run vs GWAMA by hand").
gwama_checks <- function(prefix, group, A, th) {
  a <- A$joined
  list(
    check(paste0(prefix, "variant_sets"), "Same variants (variants only in one)", length(A$only_a) + length(A$only_b), 0, "equal", group),
    check(paste0(prefix, "beta_se"), "β and se: largest absolute difference",
          max(max_abs_diff(a$beta.a, a$beta.b), max_abs_diff(a$se.a, a$se.b)), th$beta_se_max_abs_diff, group = group),
    check(paste0(prefix, "p"), "p: largest relative difference", max_rel_diff(a$p.a, a$p.b), th$p_max_rel_diff, group = group),
    check(paste0(prefix, "n_studies_direction"), "n_studies and direction: rows that differ",
          sum(a$n_studies.a != a$n_studies.b | a$direction.a != a$direction.b), 0, "equal", group),
    check(paste0(prefix, "q_i2"), "Q, its p and I²: largest absolute difference",
          max(max_abs_diff(a$q.a, a$q.b), max_abs_diff(a$het_p.a, a$het_p.b), max_abs_diff(a$i2.a, a$i2.b)), th$het_max_abs_diff, group = group),
    check(paste0(prefix, "n_eaf"), "Total N and EAF: largest absolute difference",
          max(max_abs_diff(a$n.a, a$n.b), max_abs_diff(a$eaf.a, a$eaf.b)), th$n_eaf_max_abs_diff, group = group))
}

# The re-run check: two downloads of the same analysis must have the same sha256 (NULL when the second is missing).
rerun_check <- function(id, first, second, group = NULL) {
  if (!file.exists(second)) {
    say("No %s: the re-run check is missing (run the analysis a second time)", second)
    return(NULL)
  }
  check(id, "Second site run byte-identical (0 = same sha256)", as.integer(sha256(first) != sha256(second)), 0, "equal", group)
}
