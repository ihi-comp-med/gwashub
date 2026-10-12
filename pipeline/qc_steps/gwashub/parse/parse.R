# Parse (GWASHub default step, R): the only step that reads the upload as received. It reports on the data as it
# arrived (missingness and out-of-bounds values per column, indels, chromosomes present), wrangles it into the GWASHub
# format (_lib/common.py STANDARD: chr pos ea oa beta se p eaf [n ncase ncontrol rsid info], positions in the uploaded
# build; P from BETA/SE where P is missing, N from cases + controls or the study details), and removes the rows it
# can't recover: a required value (chr pos ea oa beta se p eaf) missing or invalid, or EAF of exactly 0 or 1.
# Invalid optional values become NA (gwashub/filter decides what to do with those).
#
# Refactored from hermes3_dcm scripts/gwas_qc_parse.R (commit ff35109).
#
# -----------------------------------------------------------------------------------------------------------------
# Contract
# -----------------------------------------------------------------------------------------------------------------
# The parameters, their labels, limits and help text are in step.yaml in this folder. The contract every QC step
# follows is in CONTRIBUTING.md, section 2.
#
#   Rscript parse.R --input working.tsv --resources DIR --params params.json --log step_log.json
#
# Inputs
#   --input       The upload byte for byte (the runner copies it to working.tsv): any delimiter fread can detect,
#                 gzipped or not, any column names. Rewritten in place (see Outputs).
#   --resources   Not used.
#   --params      JSON. Dataset fields from the API (api/gwashub_api/jobs.py dataset_params):
#                   column_mapping   {standard name: header in the upload}; standard names are MAPPABLE below.
#                                    chr, pos, ea, oa, beta, se, eaf and one of p / log10p are required.
#                   effect_type      "beta" (log-odds or a linear effect) or "or" (odds ratio)
#                   n_cases, n_controls, n_total   study-level counts, or null
#                   build, ancestry, trait_type, cohort, phenotype, sex   not used by this step
#                 and, under "step", this step's parameters (DEFAULTS below).
#   Environment   GWASHUB_ARTIFACT_DIR: where the artifacts go (a temporary folder when unset).
#
# Outputs
#   --input       The GWASHub format: tab-separated, header, "NA" for missing, columns in OUT_ORDER (optional ones
#                 only when mapped or filled): chr pos ea oa beta se p eaf [n ncase ncontrol rsid info].
#                   chr      1-22, X, Y (no "chr" prefix)
#                   pos      integer, in the uploaded build (harmonise_liftover moves it to GRCh38)
#                   ea, oa   upper-case A/C/G/T strings; not yet checked against the reference genome
#                   beta     log-odds for "or" uploads, otherwise as uploaded
#                   p        in (0, 1]; filled from BETA/SE (or -log10 P) where missing
#                   eaf      in (0, 1), frequency of ea as uploaded
#                   n, ncase, ncontrol   whole numbers
#   --log         {"step": "parse", "verdict", "metrics", "messages", "artifacts"}
#   Artifacts     parse_summary.tsv     per mapped column: missing and out-of-bounds counts as received, values kept
#                 chromosomes.tsv       each chromosome label as received, what it was read as, and its row count
#                 removed_rows.tsv.gz   only when rows were removed: the mapped columns as uploaded (text), line (1 =
#                                       first data row) and reason ("; "-separated when more than one applies)
#
# Verdict
#   fail   a required column not mapped or not in the file; a truncated gzip; the values contradict effect_type;
#          the P column looks like -log10 P; fewer than min_rows rows left; more than fail_drop_fraction removed
#   warn   more than warn_drop_fraction removed; BETA, SE or P rounded; N above the declared total; an autosome
#          absent from a genome-wide file
#   pass   otherwise. The script always exits 0 once the log is written; the runner reports any other exit as a crash.
#
# -----------------------------------------------------------------------------------------------------------------
# Notes for whoever maintains this
# -----------------------------------------------------------------------------------------------------------------
# - Order matters. The odds-ratio checks run on the values before conversion, and the -log10 P check runs before the
#   validity rules, which would otherwise turn every P above 1 into NA and the row into a "P outside 0-1" removal.
# - For odds-ratio uploads the SE is taken to be the SE of log(OR), which is what PLINK, REGENIE, SAIGE and most
#   cohort pipelines report next to an OR. A file that gives the delta-method SE of the OR itself (about OR x SE of
#   log OR) is not caught here: gwashub/stats_summary catches it, since BETA/SE then disagrees with P.
# - A P filled in from BETA/SE uses the normal approximation. For small samples (t statistics) or for tests with a
#   saddlepoint or Firth correction (SAIGE, REGENIE) it is less accurate than the P the cohort computed, which is why
#   an uploaded P always wins and the computed one only fills gaps.
# - P = 0 becomes the smallest positive double (about 2.2e-308) so gwas2vcf keeps the row. The true P is unknown
#   below that; anything that works on -log10 P is capped near 307 for those rows.
# - EAF of exactly 0 or 1 means the variant was monomorphic in the sample (or EAF was rounded to 0 or 1); its effect
#   estimate carries no information.
# - Out-of-bounds values become NA. Required columns then lose the row (with a reason); optional columns keep the row.
#   A P that isn't a number at all (text) counts as missing and is computed from BETA/SE; a numeric P outside 0-1 does
#   not, and the row is removed.
suppressPackageStartupMessages({ library(data.table); library(jsonlite) })

DEFAULTS <- list(
  fill_counts = TRUE,         # missing N / cases / controls come from the dataset's study details
  p_from_log10p = "beta_se",  # how a missing P is made: "beta_se" (as hermes3_dcm) or "log10p" (when -log10 P is mapped)
  neglog10p_fraction = 0.01,  # P values above 1 in more than this share of rows: the column is -log10 P
  min_rows = 1000,
  warn_drop_fraction = 0.01,
  fail_drop_fraction = 0.5,
  min_distinct = 1000         # BETA, SE or P with fewer distinct values than this (in 50,000+ rows) look rounded
)
MAPPABLE <- c("rsid", "chr", "pos", "ea", "oa", "eaf", "beta", "se", "p", "log10p", "n", "ncase", "ncontrol", "info")
REQUIRED <- c("chr", "pos", "ea", "oa", "beta", "se", "p", "eaf")
OUT_ORDER <- c("chr", "pos", "ea", "oa", "beta", "se", "p", "eaf", "n", "ncase", "ncontrol", "rsid", "info")
CHROMS <- c(as.character(1:22), "X", "Y")
LARGE <- 50000  # rows from which a file is taken to be genome-wide (the missing-autosome check)

args <- commandArgs(trailingOnly = TRUE)
opt <- setNames(as.list(args[c(FALSE, TRUE)]), sub("^--", "", args[c(TRUE, FALSE)]))
params <- fromJSON(opt$params, simplifyVector = FALSE)
sp <- modifyList(DEFAULTS, params$step %||% list())
art_dir <- Sys.getenv("GWASHUB_ARTIFACT_DIR", tempdir())
dir.create(art_dir, showWarnings = FALSE, recursive = TRUE)

write_log <- function(verdict, metrics = list(), messages = character(), artifacts = list()) {
  write_json(list(step = "parse", verdict = verdict, metrics = metrics, messages = as.list(messages), artifacts = artifacts),
             opt$log, auto_unbox = TRUE, digits = NA, null = "null")
}
stop_with <- function(msg, metrics = list()) {
  write_log("fail", metrics, msg)
  quit(save = "no", status = 0)
}
num <- function(x) suppressWarnings(as.numeric(x))
count <- function(x) {  # "<=40", "1,234", " 500 " -> number (hermes3_dcm stripped the non-numeric characters)
  suppressWarnings(as.numeric(gsub("^[^0-9.]+|[^0-9.]+$", "", gsub(",", "", trimws(x)))))
}

#=============================================================================
# column mapping
#=============================================================================
colmap <- Filter(function(v) !is.null(v) && nzchar(v), params$column_mapping %||% list())
colmap <- colmap[intersect(names(colmap), MAPPABLE)]
missing <- setdiff(setdiff(REQUIRED, "p"), names(colmap))
if (!any(c("p", "log10p") %in% names(colmap))) missing <- c(missing, "p (or -log10 P)")
if (length(missing)) stop_with(sprintf("Required column(s) not mapped: %s. Re-map columns and re-run.", paste(missing, collapse = ", ")))

#=============================================================================
# read the upload (any delimiter, gzipped or not), mapped columns only, as text
#=============================================================================
con <- file(opt$input, "rb"); magic <- readBin(con, "raw", 2); close(con)
gz <- length(magic) == 2 && magic[1] == as.raw(0x1f) && magic[2] == as.raw(0x8b)
# A cut-short upload still decompresses up to the break, so it would otherwise pass as a smaller file.
if (gz && system2("gzip", c("-t", shQuote(opt$input)), stdout = FALSE, stderr = FALSE) != 0) {
  stop_with("The gzip file is incomplete or damaged (gzip -t fails): it was probably cut short. Upload it again.")
}
src <- if (gz) list(cmd = sprintf("gzip -dc '%s'", opt$input)) else list(file = opt$input)
header <- names(do.call(fread, c(src, list(nrows = 0))))
absent <- setdiff(unique(unlist(colmap)), header)
if (length(absent)) stop_with(sprintf("Mapped column(s) not in the file: %s.", paste(absent, collapse = ", ")))
raw <- do.call(fread, c(src, list(select = unique(unlist(colmap)), colClasses = "character",
                                  na.strings = c("", "NA", "NaN", "nan", "."), showProgress = FALSE)))
gwas <- as.data.table(lapply(colmap, function(col) raw[[col]]))
rows_in <- nrow(gwas)
# Header lines inside the data: files joined together with their headers (removed below as invalid values).
header_line <- !is.na(gwas$chr) & !is.na(gwas$pos) & gwas$chr == colmap$chr & gwas$pos == colmap$pos
# Kept until the end: the removed rows are written as received (raw), with their line in the file and the reasons.
was_missing <- lapply(gwas[, intersect(REQUIRED, names(gwas)), with = FALSE], is.na)
cat(sprintf("Read %s rows; columns: %s\n", format(rows_in, big.mark = ","), paste(names(gwas), collapse = ", ")))

# missingness as received
summary <- data.table(column = names(gwas), mapped_from = unlist(colmap)[names(gwas)],
                      missing = sapply(gwas, function(x) sum(is.na(x))))

#=============================================================================
# recoding (as hermes3_dcm)
#=============================================================================
messages <- character()
metrics <- list(rows_in = rows_in, columns_mapped = length(colmap))
warnings <- character()  # problems that make the verdict warn
if (any(header_line)) {
  metrics$header_lines <- sum(header_line)
  messages <- c(messages, sprintf("%s repeated header lines (were files joined together?) are removed.", format(sum(header_line), big.mark = ",")))
}

# chromosome: drop a "chr" prefix, upper case, 23 -> X, 24 -> Y, and PLINK's 25 / XY (the X pseudo-autosomal regions,
# in X coordinates) -> X; anything else (MT, contigs) is invalid
received_chr <- gwas$chr
gwas[, chr := toupper(sub("(?i)^chr", "", trimws(chr), perl = TRUE))]
par_rows <- sum(gwas$chr %in% c("25", "XY"))
gwas[, chr := fcase(chr %in% CHROMS, chr, chr == "23", "X", chr == "24", "Y", chr %in% c("25", "XY"), "X", default = NA_character_)]
if (par_rows) messages <- c(messages, sprintf("%s rows on chromosome 25 / XY (pseudo-autosomal) are read as X.", format(par_rows, big.mark = ",")))
chroms <- data.table(as_received = received_chr, chromosome = gwas$chr)[, .(rows = .N), by = .(as_received, chromosome)]
chroms[, chromosome := fifelse(is.na(chromosome), "(removed: not 1-22, X, Y)", chromosome)]
setorder(chroms, chromosome, as_received)
fwrite(chroms, file.path(art_dir, "chromosomes.tsv"), sep = "\t", na = "NA")
present <- intersect(CHROMS, unique(gwas$chr))
messages <- c(messages, sprintf("Chromosomes present: %s%s.", if (length(present)) paste(present, collapse = ", ") else "none",
                                if (length(setdiff(CHROMS, present))) sprintf(" (absent: %s)", paste(setdiff(CHROMS, present), collapse = ", ")) else ""))
other <- sum(is.na(gwas$chr) & !is.na(received_chr))
if (other) messages <- c(messages, sprintf("%s rows on other chromosomes or contigs (%s) are removed.", format(other, big.mark = ","),
                                          paste(head(unique(received_chr[is.na(gwas$chr) & !is.na(received_chr)]), 5), collapse = ", ")))
metrics$chromosomes_present <- length(present)

# sample sizes: strip non-numeric characters ("<=40" -> 40); fill from the study details
for (col in intersect(c("n", "ncase", "ncontrol"), names(gwas))) gwas[, (col) := count(get(col))]
# N from the row's own cases + controls where N is missing
if (all(c("ncase", "ncontrol") %in% names(gwas))) {
  if (!"n" %in% names(gwas)) gwas[, n := NA_real_]
  k <- gwas[is.na(n) & !is.na(ncase) & !is.na(ncontrol), .N]
  if (k) {
    gwas[is.na(n) & !is.na(ncase) & !is.na(ncontrol), n := ncase + ncontrol]
    metrics$n_from_cases_controls <- k
    messages <- c(messages, sprintf("Filled N for %s rows from their cases + controls.", format(k, big.mark = ",")))
  }
}
if (isTRUE(sp$fill_counts)) {
  # Case/control datasets give cases and controls; quantitative ones only the total (n_total).
  cases <- params$n_cases; controls <- params$n_controls
  total <- params$n_total %||% (if (!is.null(cases) && !is.null(controls)) cases + controls else NULL)
  for (fill in list(list("n", total), list("ncase", cases), list("ncontrol", controls))) {
    col <- fill[[1]]; value <- fill[[2]]
    if (is.null(value)) next
    if (!col %in% names(gwas)) gwas[, (col) := NA_real_]
    k <- gwas[is.na(get(col)), .N]
    if (k) {
      gwas[is.na(get(col)), (col) := as.numeric(value)]
      metrics[[paste0("filled_", col)]] <- k
      messages <- c(messages, sprintf("Filled %s for %s rows from the study details (%s).", col, format(k, big.mark = ","), format(value, big.mark = ",")))
    }
  }
}

# numbers
for (col in intersect(c("pos", "eaf", "beta", "se", "p", "log10p", "info"), names(gwas))) gwas[, (col) := num(get(col))]

# rounded values: a few hundred distinct SEs in a million rows means 2 or 3 decimal places, which meta-analysis can't undo
# The damage is to the inverse-variance weights and to small effects: an SE of 0.01 written as "0.01" may be anything
# from 0.005 to 0.015: the SE is out by up to 50% and its inverse-variance weight (1/SE^2) by up to a factor of 4.
rounded <- character()
for (col in intersect(c("beta", "se", "p"), names(gwas))) {
  x <- gwas[[col]][is.finite(gwas[[col]])]
  if (length(x) >= 50000 && uniqueN(x) < sp$min_distinct) {
    rounded <- c(rounded, toupper(col))
    warnings <- c(warnings, sprintf("%s has only %s distinct values in %s rows: it looks rounded, which loses precision (most for small values). Upload unrounded values if you can.",
                                    toupper(col), format(uniqueN(x), big.mark = ","), format(length(x), big.mark = ",")))
  }
}
if (length(rounded)) metrics$rounded_columns <- paste(rounded, collapse = ", ")

# the effect type chosen vs what the column holds: odds ratios are positive and centred on 1, log-odds on 0
# Under the null, about half of the log-odds (or linear effects) are negative whichever allele is the effect allele, so
# 99% positive with a median between 0.5 and 2 is not something a real BETA column does. An OR column can't have
# values of 0 or below; the 1% allowance is for the odd corrupt row, not for real ORs.
effect_or <- tolower(params$effect_type %||% "beta") == "or"
b <- gwas$beta[is.finite(gwas$beta)]
if (length(b) >= 100) {
  if (!effect_or && mean(b > 0) > 0.99 && median(b) > 0.5 && median(b) < 2) {
    stop_with(sprintf("Every BETA is positive with a median of %.3f: these look like odds ratios. Set the effect type to odds ratio and re-run.", median(b)),
              list(rows_in = rows_in, beta_median = median(b)))
  }
  if (effect_or && mean(b <= 0) > 0.01) {
    stop_with(sprintf("%.1f%% of the odds ratios are 0 or negative: the column looks like log-odds (BETA). Set the effect type to BETA and re-run.", 100 * mean(b <= 0)),
              list(rows_in = rows_in, or_not_positive_fraction = mean(b <= 0)))
  }
}

# odds ratios -> log odds
if (effect_or) {
  gwas[, beta := fifelse(is.finite(beta) & beta > 0, log(beta), NA_real_)]
  metrics$or_to_beta <- gwas[!is.na(beta), .N]
  messages <- c(messages, "Converted odds ratios to log-odds (BETA).")
}

# a "P" column holding -log10(P)
if ("p" %in% names(gwas)) {
  above <- gwas[!is.na(p), mean(p > 1)]
  if (!is.na(above) && above > sp$neglog10p_fraction) {
    stop_with(sprintf("%.1f%% of P values are above 1: the P column looks like -log10(P). Map it as -log10 P instead and re-run.", 100 * above),
              list(rows_in = rows_in, p_above_one_fraction = above))
  }
}

# P missing as received (computed below); an out-of-bounds P is not "missing": it is reported and the row removed
p_missing <- if ("p" %in% names(gwas)) is.na(gwas$p) else rep(TRUE, nrow(gwas))

# alleles: upper case A/C/G/T only
for (col in c("ea", "oa")) gwas[, (col) := toupper(trimws(get(col)))]
# alleles coded as numbers (PLINK 1/2) or indel letters (I/D, R/D) can't be matched to the genome: say so by name
for (codes in list(list(c("0", "1", "2"), "1/2"), list(c("I", "D", "R"), "I/D"))) {
  k <- sum(gwas$ea %in% codes[[1]] | gwas$oa %in% codes[[1]])
  if (k > 0.01 * rows_in) {
    messages <- c(messages, sprintf("%s rows have alleles coded %s rather than bases: recode them to A/C/G/T before uploading.",
                                    format(k, big.mark = ","), codes[[2]]))
  }
}

#=============================================================================
# validity: invalid values become NA (as hermes3_dcm's per-column rules)
#=============================================================================
valid <- list(
  chr      = function(x) !is.na(x),
  pos      = function(x) !is.na(x) & x > 0 & x == round(x),
  ea       = function(x) grepl("^[ACGT]+$", x),
  oa       = function(x) grepl("^[ACGT]+$", x),
  eaf      = function(x) !is.na(x) & x >= 0 & x <= 1,
  beta     = function(x) is.finite(x),
  se       = function(x) is.finite(x) & x > 0,
  p        = function(x) !is.na(x) & x >= 0 & x <= 1,
  log10p   = function(x) is.finite(x) & x >= 0,
  n        = function(x) is.finite(x) & x > 0,
  ncase    = function(x) is.finite(x) & x > 0,
  ncontrol = function(x) is.finite(x) & x > 0,
  info     = function(x) !is.na(x) & x >= 0 & x <= 1,
  rsid     = function(x) !is.na(x)
)
summary[, out_of_bounds := sapply(column, function(col) sum(!is.na(gwas[[col]]) & !valid[[col]](gwas[[col]])))]
for (col in names(gwas)) gwas[!valid[[col]](get(col)), (col) := NA]
same <- !is.na(gwas$ea) & !is.na(gwas$oa) & gwas$ea == gwas$oa  # both checked: an NA here breaks the removal reasons
identical_alleles <- sum(same)
gwas[same, c("ea", "oa") := NA_character_]

# EAF of exactly 0 or 1 (monomorphic): no information, and harmonisation can't use them; removed
mono <- !is.na(gwas$eaf) & (gwas$eaf == 0 | gwas$eaf == 1)
monomorphic <- sum(mono)
gwas[mono, eaf := NA_real_]
if (monomorphic) messages <- c(messages, sprintf("%s rows with EAF of 0 or 1 (monomorphic) are removed.", format(monomorphic, big.mark = ",")))

# P where it is missing: from -log10 P (when mapped and chosen) or BETA / SE (hermes3_dcm: 2 * pnorm(-|beta / se|))
if (!"p" %in% names(gwas)) gwas[, p := NA_real_]
need <- p_missing & is.na(gwas$p)
from_log <- 0L
if (sp$p_from_log10p == "log10p" && "log10p" %in% names(gwas)) {
  use <- need & !is.na(gwas$log10p)
  from_log <- sum(use)
  gwas[use, p := 10^(-log10p)]
  need <- need & !use
}
use <- need & !is.na(gwas$beta) & !is.na(gwas$se)
gwas[use, p := 2 * pnorm(-abs(beta / se))]
filled_p <- from_log + sum(use)
if (from_log) messages <- c(messages, sprintf("P missing for %s rows: computed from -log10 P.", format(from_log, big.mark = ",")))
if (sum(use)) messages <- c(messages, sprintf("P missing for %s rows: computed from BETA / SE (normal approximation).", format(sum(use), big.mark = ",")))
metrics$p_computed <- filled_p

# P = 0 -> the smallest positive double (as hermes3_dcm); gwas2vcf needs P > 0
zero_p <- gwas[!is.na(p) & p == 0, .N]
gwas[!is.na(p) & p == 0, p := .Machine$double.xmin]
if (zero_p) messages <- c(messages, sprintf("Recoded %s P values of 0 to %g.", format(zero_p, big.mark = ","), .Machine$double.xmin))

#=============================================================================
# drop rows without the required values; write the GWASHub format
#=============================================================================
keep <- stats::complete.cases(gwas[, ..REQUIRED])
# Why each removed row goes: every required value it lacks, in the order of REQUIRED ("; "-separated).
INVALID <- c(chr = "CHR not 1-22, X or Y", pos = "POS not a positive whole number", ea = "EA not A/C/G/T",
             oa = "OA not A/C/G/T", beta = if (tolower(params$effect_type %||% "beta") == "or") "OR not a positive number" else "BETA not a number",
             se = "SE not a positive number", p = "P outside 0-1", eaf = "EAF outside 0-1")
reasons <- character(rows_in)
add <- function(mask, label) {
  mask <- mask & !keep
  reasons[mask] <<- paste0(reasons[mask], ifelse(nzchar(reasons[mask]), "; ", ""), label)
}
for (col in REQUIRED) {
  lost <- is.na(gwas[[col]])
  if (col %in% c("ea", "oa")) lost <- lost & !same
  if (col == "eaf") lost <- lost & !mono
  miss <- was_missing[[col]] %||% rep(TRUE, rows_in)
  add(lost & miss, if (col == "p") "P missing and not computable" else sprintf("%s missing", toupper(col)))
  add(lost & !miss, INVALID[[col]])
  if (col == "oa") add(same, "EA and OA identical")
  if (col == "eaf") add(mono, "EAF 0 or 1 (monomorphic)")
}
reasons[header_line] <- "repeated header line"
removed_rows <- raw[!keep]
removed_rows[, `:=`(line = which(!keep), reason = reasons[!keep])]
setcolorder(removed_rows, "line")
rm(raw); invisible(gc())
gwas <- gwas[keep]
rows_out <- nrow(gwas)
dropped <- rows_in - rows_out
summary[, `:=`(missing_pct = round(100 * missing / max(rows_in, 1), 2), out_of_bounds_pct = round(100 * out_of_bounds / max(rows_in, 1), 2))]
summary[, kept_non_missing := sapply(column, function(col) if (col %in% names(gwas)) sum(!is.na(gwas[[col]])) else 0L)]
setcolorder(summary, c("column", "mapped_from", "missing", "missing_pct", "out_of_bounds", "out_of_bounds_pct", "kept_non_missing"))
fwrite(summary, file.path(art_dir, "parse_summary.tsv"), sep = "\t")
# Removed rows by reason: a required column missing or out of bounds as received (a row can have more than one).
for (i in seq_len(nrow(summary))) {
  r <- summary[i]
  if (r$missing + r$out_of_bounds == 0) next
  messages <- c(messages, sprintf("%s: %s missing (%.2f%%), %s out of bounds (%.2f%%)%s.", toupper(r$column), format(r$missing, big.mark = ","),
                                  r$missing_pct, format(r$out_of_bounds, big.mark = ","), r$out_of_bounds_pct,
                                  if (r$column %in% REQUIRED) "; those rows are removed" else "; set to missing"))
}

gwas[, pos := as.integer(pos)]
for (col in intersect(c("n", "ncase", "ncontrol"), names(gwas))) gwas[, (col) := round(get(col))]
gwas <- gwas[, intersect(OUT_ORDER, names(gwas)), with = FALSE]
fwrite(gwas, opt$input, sep = "\t", na = "NA", quote = FALSE)

# N above the study's total: N is per-variant (it can be lower), never higher
# The 5% margin allows for a declared total that was rounded or taken from a slightly older freeze. An effective N
# (4 / (1/cases + 1/controls)) is never above the total, so a column of those doesn't trip this either.
declared <- params$n_total %||% (if (!is.null(params$n_cases) && !is.null(params$n_controls)) params$n_cases + params$n_controls else NULL)
if (!is.null(declared) && "n" %in% names(gwas)) {
  k <- gwas[!is.na(n) & n > 1.05 * declared, .N]
  if (k) {
    metrics$n_above_total <- k
    warnings <- c(warnings, sprintf("N is above the study's total sample size (%s) for %s rows (up to %s): check the N column and the study details.",
                                    format(declared, big.mark = ","), format(k, big.mark = ","), format(max(gwas$n, na.rm = TRUE), big.mark = ",")))
  }
}
# EAF never above 0.5: fine when the effect allele is always the minor one (PLINK 1.9's A1), wrong if it is a MAF column
if (rows_out >= sp$min_rows && max(gwas$eaf) <= 0.5) {
  messages <- c(messages, "No EAF is above 0.5: either the effect allele is always the minor allele (as PLINK 1.9's A1), or the column is the minor allele frequency rather than the effect allele's. The EAF check tells these apart.")
}
# an autosome missing from a genome-wide file: usually a file cut short or a chromosome left out when joining files
absent <- setdiff(as.character(1:22), unique(gwas$chr))
if (rows_out >= LARGE && length(absent)) {
  metrics$autosomes_absent <- length(absent)
  warnings <- c(warnings, sprintf("No variants on chromosome %s: was the file cut short, or a chromosome left out when files were joined?", paste(absent, collapse = ", ")))
}

indels <- gwas[nchar(ea) > 1 | nchar(oa) > 1, .N]
messages <- c(messages, sprintf("Indels: %s (%.2f%%); kept (gwashub/filter can remove them).", format(indels, big.mark = ","), 100 * indels / max(rows_out, 1)))
metrics <- c(metrics, list(rows_out = rows_out, rows_removed = dropped, p_zero_recoded = zero_p,
                           identical_alleles = identical_alleles, eaf_monomorphic = monomorphic, indels = indels))
frac <- if (rows_in) dropped / rows_in else 1
messages <- c(sprintf("Parsed %s of %s rows (%.2f%% removed: a required value missing or out of bounds, or EAF 0 or 1).",
                      format(rows_out, big.mark = ","), format(rows_in, big.mark = ","), 100 * frac), messages)
verdict <- "pass"
if (rows_out < sp$min_rows) {
  verdict <- "fail"
  messages <- c(messages, sprintf("Fewer than %s usable rows.", format(sp$min_rows, big.mark = ",")))
} else if (frac > sp$fail_drop_fraction) {
  verdict <- "fail"
  messages <- c(messages, sprintf("More than %.0f%% of rows removed: check the column mapping.", 100 * sp$fail_drop_fraction))
} else if (frac > sp$warn_drop_fraction || length(warnings)) {
  verdict <- "warn"
}
messages <- c(messages, warnings)
artifacts <- list(list(type = "table", path = "parse_summary.tsv", label = "Columns: missing and out-of-bounds values", rows = nrow(summary)),
                  list(type = "table", path = "chromosomes.tsv", label = "Chromosomes present", rows = nrow(chroms)))
if (dropped) {
  fwrite(removed_rows, file.path(art_dir, "removed_rows.tsv.gz"), sep = "\t", na = "NA", quote = FALSE)
  artifacts[[3]] <- list(type = "table", path = "removed_rows.tsv.gz", label = "Removed rows", rows = dropped,
                         description = "Every removed row as uploaded (the mapped columns), its line in the file (1 = first data row) and why it was removed.")
}
write_log(verdict, metrics, messages, artifacts)
