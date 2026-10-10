# Filter (GWASHub default step, R): removes variants by value thresholds. GWASHub format in and out.
# Each value filter (|BETA|, SE, INFO, MAF, N, MAC) is a range parameter (pipeline/runner/params.py): its own on/off switch,
# and a minimum and/or maximum each switched on or off; variants are kept when min <= value <= max. The |BETA| default
# follows the HERMES 3 DCM pipeline (hermes3_dcm scripts/gwas_qc_parse.R, commit ff35109). EAF checks (missing, 0 or 1)
# are parse's. Missing INFO is filled with 1 by default (HERMES 3 removed those variants: "remove the variant").
suppressPackageStartupMessages({ library(data.table); library(jsonlite) })

DEFAULTS <- list(
  chromosomes = "1-22, X, Y",
  beta = list(on = TRUE, min_on = FALSE, min = NULL, max_on = TRUE, max = 20),
  se = list(on = FALSE, min_on = FALSE, min = 0, max_on = TRUE, max = 10),
  info = list(on = FALSE, min_on = TRUE, min = 0.8, max_on = FALSE, max = 1),
  missing_info = "fill with 1",
  maf = list(on = FALSE, min_on = TRUE, min = 0.01, max_on = FALSE, max = 0.5),
  n = list(on = FALSE, min_on = TRUE, min = 100, max_on = FALSE, max = 1e7),
  mac = list(on = FALSE, min_on = TRUE, min = 6, max_on = FALSE, max = 1e7),  # minor allele count, 2 x MAF x N
  palindromic = list(on = FALSE, min_on = FALSE, min = 0, max_on = TRUE, max = 0.4),
  drop_indels = FALSE,
  drop_duplicates = TRUE,  # keep the first of variants with the same chr:pos:alleles (alleles in either order)
  warn_removed_fraction = 0.05
)
CHROM_SETS <- list("1-22" = as.character(1:22), "1-22, X" = c(as.character(1:22), "X"),
                   "1-22, X, Y" = c(as.character(1:22), "X", "Y"))

args <- commandArgs(trailingOnly = TRUE)
opt <- setNames(as.list(args[c(FALSE, TRUE)]), sub("^--", "", args[c(TRUE, FALSE)]))
params <- fromJSON(opt$params, simplifyVector = FALSE)
sp <- modifyList(DEFAULTS, params$step %||% list())
art_dir <- Sys.getenv("GWASHUB_ARTIFACT_DIR", tempdir())
dir.create(art_dir, showWarnings = FALSE, recursive = TRUE)

write_log <- function(verdict, metrics, messages, artifacts = list()) {
  write_json(list(step = "filter", verdict = verdict, metrics = metrics, messages = as.list(messages), artifacts = artifacts),
             opt$log, auto_unbox = TRUE, digits = NA, null = "null")
}

gwas <- fread(opt$input, sep = "\t", na.strings = "NA", colClasses = list(character = c("chr", "ea", "oa")), showProgress = FALSE)
rows_in <- nrow(gwas)
has <- function(col) col %in% names(gwas) && any(!is.na(gwas[[col]]))
on <- function(r) isTRUE(r$on) && (isTRUE(r$min_on) || isTRUE(r$max_on))

# A filter that needs a column the file doesn't have would remove everything: say so instead.
problems <- character()
for (f in list(list("info", "INFO"), list("n", "N"), list("eaf", "MAF"), list("n", "MAC"))) {
  r <- sp[[switch(f[[2]], MAF = "maf", MAC = "mac", f[[1]])]]
  if (on(r) && !has(f[[1]])) problems <- c(problems, sprintf("The %s filter is on but the dataset has no %s: turn the filter off.", f[[2]], toupper(f[[1]])))
}
if (length(problems)) {
  write_log("fail", list(rows_in = rows_in), problems)
  quit(save = "no", status = 0)
}

# Missing INFO: filled with 1 (perfectly imputed) or those variants removed (a rule below).
filled_info <- 0L
if (has("info") && sp$missing_info == "fill with 1") {
  filled_info <- gwas[is.na(info), .N]
  if (filled_info) gwas[is.na(info), info := 1]
}

# TRUE where a value is outside the range's switched-on limits (missing values are left to other rules).
outside <- function(x, r) {
  drop <- rep(FALSE, length(x))
  if (!isTRUE(r$on)) return(drop)
  if (isTRUE(r$min_on)) drop <- drop | (!is.na(x) & x < r$min)
  if (isTRUE(r$max_on)) drop <- drop | (!is.na(x) & x > r$max)
  drop
}
limits <- function(r) paste(c(if (isTRUE(r$min_on)) sprintf("min %g", r$min), if (isTRUE(r$max_on)) sprintf("max %g", r$max)), collapse = ", ")
none <- function(d) rep(FALSE, nrow(d))

# Each rule: a label and the rows it removes (evaluated in order on what is left).
rules <- list(
  list("chromosome not in the kept set", function(d) !d$chr %in% CHROM_SETS[[sp$chromosomes]]),
  list(sprintf("|BETA| outside the limits (%s)", limits(sp$beta)), function(d) outside(abs(d$beta), sp$beta)),
  list(sprintf("SE outside the limits (%s)", limits(sp$se)), function(d) outside(d$se, sp$se)),
  list("INFO missing", function(d) if (sp$missing_info == "remove the variant" && "info" %in% names(d)) is.na(d$info) else none(d)),
  list(sprintf("INFO outside the limits (%s)", limits(sp$info)), function(d) if ("info" %in% names(d)) outside(d$info, sp$info) else none(d)),
  list(sprintf("MAF outside the limits (%s)", limits(sp$maf)), function(d) if ("eaf" %in% names(d)) outside(pmin(d$eaf, 1 - d$eaf), sp$maf) else none(d)),
  list(sprintf("N outside the limits (%s)", limits(sp$n)), function(d) if ("n" %in% names(d)) outside(d$n, sp$n) else none(d)),
  # EasyQC's minor allele count: a handful of copies of the minor allele gives unstable, often false-positive results
  list(sprintf("MAC outside the limits (%s)", limits(sp$mac)), function(d) {
    if (all(c("n", "eaf") %in% names(d))) outside(2 * pmin(d$eaf, 1 - d$eaf) * d$n, sp$mac) else none(d)
  }),
  # A/T and C/G SNPs read the same on both strands, so near MAF 0.5 nothing tells whether they need flipping.
  list(sprintf("strand-ambiguous SNP with MAF outside the limits (%s)", limits(sp$palindromic)), function(d) {
    ambiguous <- paste0(d$ea, d$oa) %in% c("AT", "TA", "CG", "GC")
    ambiguous & outside(pmin(d$eaf, 1 - d$eaf), sp$palindromic)
  }),
  list("indel", function(d) if (isTRUE(sp$drop_indels)) nchar(d$ea) > 1 | nchar(d$oa) > 1 else none(d)),
  list("duplicate variant (same chr:pos:alleles)", function(d) {
    if (!isTRUE(sp$drop_duplicates)) return(none(d))
    duplicated(data.table(d$chr, d$pos, pmin(d$ea, d$oa), pmax(d$ea, d$oa)))
  })
)
removed <- data.table(reason = sapply(rules, `[[`, 1), removed = 0L)
removed_rows <- list()  # the rows each rule removes, with the rule as the reason
for (i in seq_along(rules)) {
  drop <- rules[[i]][[2]](gwas)
  drop[is.na(drop)] <- FALSE
  removed[i, removed := sum(drop)]
  if (any(drop)) {
    removed_rows[[length(removed_rows) + 1]] <- gwas[drop][, reason := rules[[i]][[1]]]
    gwas <- gwas[!drop]
  }
}
fwrite(gwas, opt$input, sep = "\t", na = "NA", quote = FALSE)
fwrite(removed, file.path(art_dir, "filter_summary.tsv"), sep = "\t")
artifacts <- list(list(type = "table", path = "filter_summary.tsv", label = "Rows removed by each filter", rows = nrow(removed)))
if (length(removed_rows)) {
  removed_rows <- rbindlist(removed_rows)
  fwrite(removed_rows, file.path(art_dir, "removed_rows.tsv.gz"), sep = "\t", na = "NA", quote = FALSE)
  artifacts[[2]] <- list(type = "table", path = "removed_rows.tsv.gz", label = "Removed rows", rows = nrow(removed_rows),
                         description = "Every removed variant as it was before this step, with the filter that removed it.")
}

rows_out <- nrow(gwas)
frac <- if (rows_in) (rows_in - rows_out) / rows_in else 0
# Metric names: the rule's label up to any "(limits)", e.g. removed_info_outside_the_limits.
slug <- function(x) gsub("^_|_$", "", gsub("[^a-z0-9]+", "_", tolower(sub(" \\(.*", "", x))))
metrics <- list(rows_in = rows_in, rows_out = rows_out, rows_removed = rows_in - rows_out, filled_info = filled_info)
for (i in seq_len(nrow(removed))) metrics[[paste0("removed_", slug(removed$reason[i]))]] <- removed$removed[i]
messages <- sprintf("Removed %s of %s rows (%.2f%%).", format(rows_in - rows_out, big.mark = ","), format(rows_in, big.mark = ","), 100 * frac)
for (i in which(removed$removed > 0)) messages <- c(messages, sprintf("%s: %s", removed$reason[i], format(removed$removed[i], big.mark = ",")))
if (filled_info) messages <- c(messages, sprintf("Filled missing INFO with 1 for %s rows.", format(filled_info, big.mark = ",")))
verdict <- if (rows_out == 0) "fail" else if (frac > sp$warn_removed_fraction) "warn" else "pass"
if (rows_out == 0) messages <- c(messages, "No rows left.")
write_log(verdict, metrics, messages, artifacts)
