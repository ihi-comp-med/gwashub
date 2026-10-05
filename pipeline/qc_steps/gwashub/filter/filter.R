# Filter (GWASHub default step, R): removes variants by value thresholds. GWASHub format in and out.
# The defaults reproduce the HERMES 3 DCM pipeline (hermes3_dcm scripts/gwas_qc_parse.R, commit ff35109), which
# kept chromosomes 1-22, X and Y, required |BETA| < 20 and 0 < EAF < 1, and dropped rows without EAF, N or
# (when the file has INFO) INFO. Duplicate removal is added; that pipeline had none.
suppressPackageStartupMessages({ library(data.table); library(jsonlite) })

DEFAULTS <- list(
  chromosomes = "1-22, X, Y",
  max_abs_beta = 20,       # remove |BETA| >= this (0 = off)
  max_se = 0,              # remove SE > this (0 = off)
  min_info = 0,            # remove INFO < this
  drop_missing_info = TRUE,  # remove rows without INFO when the file has INFO
  min_maf = 0,             # remove MAF < this
  drop_monomorphic = TRUE, # remove EAF of exactly 0 or 1
  require_eaf = TRUE,      # remove rows without EAF
  require_n = TRUE,        # remove rows without N
  min_n = 0,               # remove N < this
  drop_indels = FALSE,
  drop_duplicates = TRUE,  # keep the first of variants with the same chr, pos and alleles (either order)
  warn_removed_fraction = 0.5
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

# A filter that needs a column the file doesn't have would remove everything: say so instead.
problems <- character()
if (isTRUE(sp$require_eaf) && !has("eaf")) problems <- c(problems, "Require EAF is on but the dataset has no EAF: map an EAF column or turn the option off.")
if (isTRUE(sp$require_n) && !has("n")) problems <- c(problems, "Require N is on but the dataset has no N: map an N column, enter the case and control counts, or turn the option off.")
if (length(problems)) {
  write_log("fail", list(rows_in = rows_in), problems)
  quit(save = "no", status = 0)
}

# Each rule: a label and the rows it removes (evaluated in order on what is left).
rules <- list(
  list("chromosome not in the kept set", function(d) !d$chr %in% CHROM_SETS[[sp$chromosomes]]),
  list("EAF missing", function(d) if (isTRUE(sp$require_eaf)) is.na(d$eaf) else rep(FALSE, nrow(d))),
  list("EAF 0 or 1", function(d) if (isTRUE(sp$drop_monomorphic) && "eaf" %in% names(d)) !is.na(d$eaf) & (d$eaf <= 0 | d$eaf >= 1) else rep(FALSE, nrow(d))),
  list("MAF below minimum", function(d) if (sp$min_maf > 0 && "eaf" %in% names(d)) !is.na(d$eaf) & pmin(d$eaf, 1 - d$eaf) < sp$min_maf else rep(FALSE, nrow(d))),
  list("|BETA| too large", function(d) if (sp$max_abs_beta > 0) abs(d$beta) >= sp$max_abs_beta else rep(FALSE, nrow(d))),
  list("SE too large", function(d) if (sp$max_se > 0) d$se > sp$max_se else rep(FALSE, nrow(d))),
  list("INFO missing", function(d) if (isTRUE(sp$drop_missing_info) && has("info")) is.na(d$info) else rep(FALSE, nrow(d))),
  list("INFO below minimum", function(d) if (sp$min_info > 0 && "info" %in% names(d)) !is.na(d$info) & d$info < sp$min_info else rep(FALSE, nrow(d))),
  list("N missing", function(d) if (isTRUE(sp$require_n)) is.na(d$n) else rep(FALSE, nrow(d))),
  list("N below minimum", function(d) if (sp$min_n > 0 && "n" %in% names(d)) !is.na(d$n) & d$n < sp$min_n else rep(FALSE, nrow(d))),
  list("indel", function(d) if (isTRUE(sp$drop_indels)) nchar(d$ea) > 1 | nchar(d$oa) > 1 else rep(FALSE, nrow(d))),
  list("duplicate variant", function(d) {
    if (!isTRUE(sp$drop_duplicates)) return(rep(FALSE, nrow(d)))
    duplicated(data.table(d$chr, d$pos, pmin(d$ea, d$oa), pmax(d$ea, d$oa)))
  })
)
removed <- data.table(reason = sapply(rules, `[[`, 1), removed = 0L)
for (i in seq_along(rules)) {
  drop <- rules[[i]][[2]](gwas)
  drop[is.na(drop)] <- FALSE
  removed[i, removed := sum(drop)]
  if (any(drop)) gwas <- gwas[!drop]
}
fwrite(gwas, opt$input, sep = "\t", na = "NA", quote = FALSE)
fwrite(removed, file.path(art_dir, "filter_summary.tsv"), sep = "\t")

rows_out <- nrow(gwas)
frac <- if (rows_in) (rows_in - rows_out) / rows_in else 0
metrics <- list(rows_in = rows_in, rows_out = rows_out, rows_removed = rows_in - rows_out)
for (i in seq_len(nrow(removed))) metrics[[paste0("removed_", gsub("^_|_$", "", gsub("[^a-z0-9]+", "_", tolower(removed$reason[i]))))]] <- removed$removed[i]
messages <- sprintf("Removed %s of %s rows (%.2f%%).", format(rows_in - rows_out, big.mark = ","), format(rows_in, big.mark = ","), 100 * frac)
for (i in which(removed$removed > 0)) messages <- c(messages, sprintf("%s: %s", removed$reason[i], format(removed$removed[i], big.mark = ",")))
verdict <- if (rows_out == 0) "fail" else if (frac > sp$warn_removed_fraction) "warn" else "pass"
if (rows_out == 0) messages <- c(messages, "No rows left.")
write_log(verdict, metrics, messages, list(list(type = "table", path = "filter_summary.tsv", label = "Rows removed by each filter")))
