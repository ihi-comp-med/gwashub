# Test 4, step 2: the known answer. For every row of planted.tsv: kept or removed, by which step and rule, and for the
# rows kept the values the QC'd file should hold (the base file's, with its effect allele). Worked out from the inputs
# and test.yaml's rules alone; nothing here reads the site's output.
#   Rscript validation/04_qc_planted/2_reference.R
# Output: local/validation/04_qc_planted/reference/expected_rows.tsv and expected_steps.tsv.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("04_qc_planted")
r <- t$cfg$reference
problems <- setNames(t$cfg$planted$problems, vapply(t$cfg$planted$problems, `[[`, "", "id"))

base <- fread(file.path(t$root, "local", "validation", t$cfg$base), colClasses = list(character = c("chr", "ea", "oa", "rsid")))
key <- fread(t$path("inputs", "planted_key.tsv"))
planted_rows <- fread(t$path("inputs", "planted.tsv"), colClasses = "character")[rsid != "rsid"]  # the header line aside
if (nrow(planted_rows) != nrow(base) + key[copy == 2, .N]) stop("planted.tsv doesn't match the base file and key: re-run 1_prepare.R")

# Every data row: the base rows (copy 1) and the appended duplicates (copy 2).
e <- rbind(base[, .(rsid, copy = 1L)], key[copy == 2, .(rsid, copy)])
e <- merge(e, key, by = c("rsid", "copy"), all.x = TRUE)
e[is.na(problem), problem := ""]
e[, `:=`(expect = "kept", step = "", reason = "")]

# Planted rows: as test.yaml says. A duplicate's first copy (in place) is kept, the appended one removed.
for (id in setdiff(unique(e$problem), "")) {
  p <- problems[[id]]
  if (p$expect == "removed") e[problem == id, `:=`(expect = "removed", step = p$step, reason = p$reason %||% "")]
  if (p$expect == "one_kept") e[problem == id & copy == 2, `:=`(expect = "removed", step = p$step, reason = p$reason)]
}

# Unplanted rows: the natural removals, in the steps' order (parse, then filter's rules in its order).
v <- base[match(e$rsid, rsid)]
f <- v$eaf; m <- pmin(f, 1 - f)
amb <- paste0(v$ea, v$oa) %in% c("AT", "TA", "CG", "GC")
natural <- list(
  list("parse", "", f == 0 | f == 1),
  list("filter", "|BETA| outside the limits", abs(v$beta) > r$beta_max),
  list("filter", "SE outside the limits", v$se > r$se_max),
  list("filter", "MAF outside the limits", m < r$maf_min),
  list("filter", "strand-ambiguous SNP with MAF outside the limits", amb & m > r$palindromic_maf_max))
for (rule in natural) {
  hit <- e$problem == "" & e$expect == "kept" & rule[[3]]
  e[hit, `:=`(expect = "removed", step = rule[[1]], reason = rule[[2]])]
}

# The values a kept row should have: the base row's (n: filled with the study's total where planted missing).
up <- t$cfg$upload
e[, `:=`(chr = v$chr, pos = v$pos, ea = v$ea, oa = v$oa, beta = v$beta, se = v$se, eaf = v$eaf, n = v$n)]
e[problem == "n_missing", n := up$n_cases + up$n_controls]
setcolorder(e, c("rsid", "copy", "problem", "expect", "step", "reason"))
fwrite(e[order(chr, pos, copy)], t$path("reference", "expected_rows.tsv"), sep = "\t", na = "NA")

# Rows each step should remove (harmonise_liftover's also include the liftover's losses, known only from its log).
steps <- e[expect == "removed", .(removed = .N), by = step]
steps <- rbind(steps, data.table(step = "parse", removed = 1L))[, .(removed = sum(removed)), by = step]  # + the header line
fwrite(steps, t$path("reference", "expected_steps.tsv"), sep = "\t")
say("Expected: %s rows kept, %s removed (%s)", format(e[expect == "kept", .N], big.mark = ","),
    format(e[expect == "removed", .N] + 1L, big.mark = ","), paste(sprintf("%s %s", steps$step, steps$removed), collapse = ", "))
say("Unplanted rows removed by the rules: %s", paste(capture.output(print(e[problem == "" & expect == "removed", .N, by = .(step, reason)])), collapse = "\n"))
