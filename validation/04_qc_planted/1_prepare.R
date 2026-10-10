# Test 4, step 1: plant problems in a clean file, and make one upload per whole-file mistake.
#   Rscript validation/04_qc_planted/1_prepare.R
# Needs: test 1's prepared UKB file (Rscript validation/01_gwama_default/1_prepare.R).
# Output, in local/validation/04_qc_planted/inputs/:
#   planted.tsv       part A: the base file with each problem planted in its own rows (test.yaml planted)
#   planted_key.tsv   which rows got which problem: rsid, problem, copy (1 = the row in place, 2 = an appended duplicate)
#   cases/<id>.tsv    part B: one upload per whole-file case (truncated_gzip is .tsv.gz)
#   uploads.tsv       what to enter on the site for each upload
source(file.path("validation", "_lib", "common.R"))
t <- test_context("04_qc_planted")
up <- t$cfg$upload
dir.create(t$path("inputs", "cases"), showWarnings = FALSE)

base_path <- file.path(t$root, "local", "validation", t$cfg$base)
if (!file.exists(base_path)) stop("No ", base_path, ": run Rscript validation/01_gwama_default/1_prepare.R first")
base <- fread(base_path, colClasses = list(character = c("chr", "ea", "oa", "rsid")))
if (anyDuplicated(base$rsid)) stop("The base file must have one row per rsID")
say("Base: %s variants from %s", format(nrow(base), big.mark = ","), base_path)

COMP <- c(A = "T", C = "G", G = "C", T = "A")
ambiguous <- function(ea, oa) paste0(ea, oa) %in% c("AT", "TA", "CG", "GC")
maf <- function(f) pmin(f, 1 - f)

#=============================================================================
# Part A: planted problems
#=============================================================================
pl <- t$cfg$planted
d <- copy(base)[, info := up$info]
d <- d[, lapply(.SD, as.character)]  # text, so a value can be anything a file might hold
set.seed(pl$seed)
pool <- which(maf(base$eaf) >= 0.02 & !ambiguous(base$ea, base$oa))
sizes <- vapply(pl$problems, function(p) as.integer(p$rows %||% pl$rows), integer(1))
picked <- split(sample(pool, sum(sizes)), rep(vapply(pl$problems, `[[`, "", "id"), sizes))
num <- function(x) as.numeric(x)
appended <- list()
for (p in pl$problems) {
  i <- picked[[p$id]]
  switch(p$id,
    eaf_missing  = d[i, eaf := NA],
    eaf_0_1      = d[i, eaf := sample(c("0", "1"), length(i), replace = TRUE)],
    p_out        = d[i, p := "1.5"],
    beta_text    = d[i, beta := "abc"],
    chr_mt       = d[i, chr := "MT"],
    header_line  = NULL,  # inserted below
    chr_y        = d[i, chr := "Y"],
    beta_large   = d[i, beta := sample(c("25", "-25"), length(i), replace = TRUE)],
    se_large     = d[i, se := "12"],
    info_low     = d[i, info := "0.5"],
    info_missing = d[i, info := NA],
    maf_low      = d[i, eaf := "0.004"],
    n_low        = d[i, n := "50"],
    indel        = d[i, ea := paste0(ea, "T")],
    dup_exact    = { appended[[p$id]] <- d[i] },
    dup_swapped  = { appended[[p$id]] <- d[i][, `:=`(ea = oa, oa = ea, beta = as.character(-num(beta)), eaf = as.character(1 - num(eaf)))] },
    wrong_pos    = d[i, pos := as.character(num(pos) + 1e9)],
    n_missing    = d[i, n := NA],
    swapped      = d[i, `:=`(ea = oa, oa = ea, beta = as.character(-num(beta)), eaf = as.character(1 - num(eaf)))],
    minus_strand = d[i, `:=`(ea = unname(COMP[ea]), oa = unname(COMP[oa]))],
    stop("No planting rule for problem ", p$id))
}
key <- rbindlist(lapply(pl$problems, function(p) {
  if (p$id == "header_line") return(NULL)
  data.table(rsid = base$rsid[picked[[p$id]]], problem = p$id, copy = 1L)
}))
key <- rbind(key, rbindlist(lapply(names(appended), function(id) data.table(rsid = appended[[id]]$rsid, problem = id, copy = 2L))))
out <- rbind(d, rbindlist(appended))
# The header line goes in the middle of the file, as when files are joined with their headers.
h <- picked$header_line[1]
out <- rbind(out[seq_len(h - 1)], as.data.table(as.list(setNames(names(out), names(out)))), out[h:nrow(out)])
fwrite(out, t$path("inputs", "planted.tsv"), sep = "\t", na = "NA", quote = FALSE)
fwrite(key, t$path("inputs", "planted_key.tsv"), sep = "\t")
say("planted.tsv: %s rows (%s planted in %d problems, %d appended duplicates, 1 header line)",
    format(nrow(out), big.mark = ","), format(nrow(key), big.mark = ","), length(pl$problems), nrow(rbindlist(appended)))

#=============================================================================
# Part B: whole-file cases
#=============================================================================
phi <- up$n_cases / (up$n_cases + up$n_controls)
uploads <- list(data.table(file = "planted.tsv", cohort = "planted", build = up$build, effect_type = up$effect_type,
                           pipeline = t$cfg$site$planted_pipeline$name, note = ""))
for (cs in t$cfg$cases) {
  f <- copy(base)
  path <- t$path("inputs", "cases", paste0(cs$id, ".tsv"))
  switch(cs$id,
    control          = NULL,
    truncated_gzip   = NULL,  # written below
    or_as_beta       = f[, beta := exp(beta)],
    beta_as_or       = NULL,  # the upload's effect type changes, not the file
    neglog10p        = f[, p := -log10(p)],
    wrong_build      = NULL,  # the upload's build changes
    maf_as_eaf       = f[, eaf := maf(eaf)],
    swapped_columns  = f[, `:=`(ea = oa, oa = ea)],
    strand_ambiguous = f[ambiguous(ea, oa), `:=`(ea = unname(COMP[ea]), oa = unname(COMP[oa]))],
    linear_scale     = f[, `:=`(beta = beta * phi * (1 - phi), se = se * phi * (1 - phi))],
    se_scale         = f[, se := se * 3],
    rounded_se       = f[, se := round(se, 3)],
    no_chr22         = { f <- f[chr != "22"] },
    n_above_total    = f[, n := 2 * n],
    alleles_12       = { set.seed(pl$seed); f[sample(.N, 1000), `:=`(ea = "1", oa = "2")] },
    stop("No rule for case ", cs$id))
  if (cs$id == "truncated_gzip") {
    path <- paste0(path, ".gz")
    fwrite(f, path, sep = "\t", na = "NA", quote = FALSE, compress = "gzip")
    bytes <- readBin(path, "raw", file.size(path))
    writeBin(bytes[seq_len(length(bytes) %/% 2)], path)
  } else {
    fwrite(f, path, sep = "\t", na = "NA", quote = FALSE)
  }
  u <- modifyList(up, cs$upload %||% list())
  uploads[[length(uploads) + 1]] <- data.table(file = file.path("cases", basename(path)), cohort = cs$id, build = u$build,
                                               effect_type = u$effect_type, pipeline = "default",
                                               note = if (cs$id == "or_as_beta") "the wizard suggests Odds ratios: set BETA" else "")
}
uploads <- rbindlist(uploads)[, `:=`(ancestry = up$ancestry, sex = up$sex, n_cases = up$n_cases, n_controls = up$n_controls)]
fwrite(uploads, t$path("inputs", "uploads.tsv"), sep = "\t")
say("%d uploads; what to enter for each: %s", nrow(uploads), t$path("inputs", "uploads.tsv"))
print(uploads[, .(file, cohort, build, effect_type, pipeline)], class = FALSE)
say("Next: upload these to the '%s' project and run QC (see README.md).", t$cfg$site$project)
