# Parse (GWASHub default step, R): the only step that reads the upload as received.
# Picks the mapped columns, recodes common problems, checks every value and writes the GWASHub format
# (_lib/common.py STANDARD): chr pos ea oa beta se p [eaf n ncase ncontrol rsid info], positions in the
# uploaded build. Rows whose required values (chr pos ea oa beta se p) are missing or invalid are dropped;
# invalid optional values become NA (gwashub/filter decides what to do with those).
#
# Refactored from hermes3_dcm scripts/gwas_qc_parse.R (commit ff35109). The recoding and validity rules are
# those of that script; the thresholds it applied (|BETA| < 20, 0 < EAF < 1, required EAF/INFO/N) are now
# gwashub/filter's parameters, and the gwas2vcf column JSON is written by gwashub/harmonise_liftover.
suppressPackageStartupMessages({ library(data.table); library(jsonlite) })

DEFAULTS <- list(
  fill_counts = TRUE,         # missing N / cases / controls come from the dataset's study details
  p_from_log10p = "beta_se",  # how P is made when only -log10 P is given: "beta_se" (as hermes3_dcm) or "log10p"
  neglog10p_fraction = 0.01,  # P values above 1 in more than this share of rows: the column is -log10 P
  min_rows = 1000,
  warn_drop_fraction = 0.01,
  fail_drop_fraction = 0.5
)
MAPPABLE <- c("rsid", "chr", "pos", "ea", "oa", "eaf", "beta", "se", "p", "log10p", "n", "ncase", "ncontrol", "info")
REQUIRED <- c("chr", "pos", "ea", "oa", "beta", "se", "p")
OUT_ORDER <- c("chr", "pos", "ea", "oa", "beta", "se", "p", "eaf", "n", "ncase", "ncontrol", "rsid", "info")
CHROMS <- c(as.character(1:22), "X", "Y")

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
src <- if (gz) list(cmd = sprintf("gzip -dc '%s'", opt$input)) else list(file = opt$input)
header <- names(do.call(fread, c(src, list(nrows = 0))))
absent <- setdiff(unique(unlist(colmap)), header)
if (length(absent)) stop_with(sprintf("Mapped column(s) not in the file: %s.", paste(absent, collapse = ", ")))
raw <- do.call(fread, c(src, list(select = unique(unlist(colmap)), colClasses = "character",
                                  na.strings = c("", "NA", "NaN", "nan", "."), showProgress = FALSE)))
gwas <- as.data.table(lapply(colmap, function(col) raw[[col]]))
rm(raw); invisible(gc())
rows_in <- nrow(gwas)
cat(sprintf("Read %s rows; columns: %s\n", format(rows_in, big.mark = ","), paste(names(gwas), collapse = ", ")))

# missingness as received
summary <- data.table(column = names(gwas), mapped_from = unlist(colmap)[names(gwas)],
                      missing = sapply(gwas, function(x) sum(is.na(x))))

#=============================================================================
# recoding (as hermes3_dcm)
#=============================================================================
messages <- character()
metrics <- list(rows_in = rows_in, columns_mapped = length(colmap))

# chromosome: drop a "chr" prefix, upper case, 23 -> X, 24 -> Y; anything else is invalid
gwas[, chr := toupper(sub("(?i)^chr", "", trimws(chr), perl = TRUE))]
gwas[, chr := fcase(chr %in% CHROMS, chr, chr == "23", "X", chr == "24", "Y", default = NA_character_)]

# sample sizes: strip non-numeric characters ("<=40" -> 40); fill from the study details
for (col in intersect(c("n", "ncase", "ncontrol"), names(gwas))) gwas[, (col) := count(get(col))]
if (isTRUE(sp$fill_counts)) {
  cases <- params$n_cases; controls <- params$n_controls
  total <- if (!is.null(cases) && !is.null(controls)) cases + controls else NULL
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

# odds ratios -> log odds
if (tolower(params$effect_type %||% "beta") == "or") {
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

# alleles: upper case A/C/G/T only
for (col in c("ea", "oa")) gwas[, (col) := toupper(trimws(get(col)))]

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
summary[, invalid := sapply(column, function(col) sum(!is.na(gwas[[col]]) & !valid[[col]](gwas[[col]])))]
for (col in names(gwas)) gwas[!valid[[col]](get(col)), (col) := NA]
identical_alleles <- gwas[!is.na(ea) & ea == oa, .N]
gwas[!is.na(ea) & ea == oa, c("ea", "oa") := NA_character_]

# P from -log10 P when there is no usable P column (hermes3_dcm: 2 * pnorm(-|beta / se|))
if (!"p" %in% names(gwas) || all(is.na(gwas$p))) {
  if (sp$p_from_log10p == "log10p") {
    gwas[, p := 10^(-log10p)]
    messages <- c(messages, "No P column: P computed from -log10 P.")
  } else {
    gwas[, p := 2 * pnorm(-abs(beta / se))]
    messages <- c(messages, "No P column: P computed from BETA / SE (normal approximation).")
  }
  metrics$p_from_log10p <- TRUE
}

# P = 0 -> the smallest positive double (as hermes3_dcm); gwas2vcf needs P > 0
zero_p <- gwas[!is.na(p) & p == 0, .N]
gwas[!is.na(p) & p == 0, p := .Machine$double.xmin]
if (zero_p) messages <- c(messages, sprintf("Recoded %s P values of 0 to %g.", format(zero_p, big.mark = ","), .Machine$double.xmin))

#=============================================================================
# drop rows without the required values; write the GWASHub format
#=============================================================================
keep <- stats::complete.cases(gwas[, ..REQUIRED])
gwas <- gwas[keep]
rows_out <- nrow(gwas)
dropped <- rows_in - rows_out
summary[, kept_non_missing := sapply(column, function(col) if (col %in% names(gwas)) sum(!is.na(gwas[[col]])) else 0L)]
fwrite(summary, file.path(art_dir, "parse_summary.tsv"), sep = "\t")

gwas[, pos := as.integer(pos)]
for (col in intersect(c("n", "ncase", "ncontrol"), names(gwas))) gwas[, (col) := round(get(col))]
gwas <- gwas[, intersect(OUT_ORDER, names(gwas)), with = FALSE]
fwrite(gwas, opt$input, sep = "\t", na = "NA", quote = FALSE)

metrics <- c(metrics, list(rows_out = rows_out, rows_removed = dropped, p_zero_recoded = zero_p,
                           identical_alleles = identical_alleles,
                           indels = gwas[nchar(ea) > 1 | nchar(oa) > 1, .N]))
frac <- if (rows_in) dropped / rows_in else 1
messages <- c(sprintf("Parsed %s of %s rows (%.2f%% removed: a required value missing or invalid).",
                      format(rows_out, big.mark = ","), format(rows_in, big.mark = ","), 100 * frac), messages)
verdict <- "pass"
if (rows_out < sp$min_rows) {
  verdict <- "fail"
  messages <- c(messages, sprintf("Fewer than %s usable rows.", format(sp$min_rows, big.mark = ",")))
} else if (frac > sp$fail_drop_fraction) {
  verdict <- "fail"
  messages <- c(messages, sprintf("More than %.0f%% of rows removed: check the column mapping.", 100 * sp$fail_drop_fraction))
} else if (frac > sp$warn_drop_fraction) {
  verdict <- "warn"
}
write_log(verdict, metrics, messages,
          list(list(type = "table", path = "parse_summary.tsv", label = "Columns: missing and invalid values")))
