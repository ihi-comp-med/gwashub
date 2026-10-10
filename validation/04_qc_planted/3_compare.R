# Test 4, step 3: what the site did vs the known answer; write result.json, plot.json, manifest.tsv.
#   Rscript validation/04_qc_planted/3_compare.R
# Needs, in local/validation/04_qc_planted/site/ (README.md steps 4 and 5):
#   planted_qc.tsv                       the planted dataset's QC'd file
#   planted_<step>_removed_rows.tsv.gz   its removed rows from parse, filter and harmonise_liftover (step outputs)
#   planted.json                         the run's result.json, or the dataset as the API gives it (/api/datasets/<id>)
#   cases/<id>.json                      the same for each whole-file case
# and reference/ (step 2), and site_run.yaml filled in.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("04_qc_planted")
th <- t$cfg$thresholds
site_file <- function(...) {
  p <- t$path("site", ...)
  if (!file.exists(p)) stop("Missing ", p, " (see README.md)")
  p
}
steps_of <- function(path) {  # the dataset's API record (its run) or the run's own result.json
  x <- fromJSON(path, simplifyVector = FALSE)
  run <- if (!is.null(x$run)) x$run else if (!is.null(x$steps)) x else NULL
  if (is.null(run)) stop(path, " has no run: start QC on the dataset, then save /api/datasets/<id> again")
  setNames(run$steps, vapply(run$steps, function(s) sub("^gwashub/", "", s$key), ""))
}
messages_of <- function(s) unlist(s$messages) %||% character()

e <- fread(t$path("reference", "expected_rows.tsv"), colClasses = list(character = c("rsid", "chr", "ea", "oa")))
expected_steps <- fread(t$path("reference", "expected_steps.tsv"))
planted <- fread(t$path("inputs", "planted.tsv"), colClasses = "character")

#=============================================================================
# Part A: account for every row of the planted file
#=============================================================================
qc <- fread(site_file("planted_qc.tsv"), colClasses = list(character = c("chr", "ea", "oa", "rsid")))
removed_by <- function(step) {
  d <- fread(site_file(sprintf("planted_%s_removed_rows.tsv.gz", step)), colClasses = "character")
  d[, step := step]
}
parse_rm <- removed_by("parse")
filter_rm <- removed_by("filter")
harm_rm <- removed_by("harmonise_liftover")
# harmonise_liftover's removed rows carry their position in its input (the uploaded build), not the rsID.
at <- unique(planted[rsid != "rsid", .(rsid, chr, pos)], by = c("chr", "pos"))
harm_rm <- merge(harm_rm, at, by.x = c("chr_input", "pos_input"), by.y = c("chr", "pos"), all.x = TRUE)
removed <- rbind(parse_rm[rsid != "rsid", .(rsid, step, reason)], filter_rm[, .(rsid, step, reason)],
                 harm_rm[, .(rsid, step, reason = paste(stage, reason, sep = ": "))])

# Observed outcome per expected row. A duplicate's two copies share an rsID: the first copy takes the QC'd row,
# the second the removal.
qc[, qc_row := .I]
first_qc <- qc[!duplicated(rsid)]
e[, `:=`(observed = "unaccounted", obs_step = "", obs_reason = "", renamed = FALSE)]
e[, qc_row := fifelse(copy == 1L, first_qc$qc_row[match(rsid, first_qc$rsid)], NA_integer_)]
e[!is.na(qc_row), observed := "kept"]
rm_seq <- removed[, .(step, reason, nth = seq_len(.N)), by = rsid]
for (j in 1:2) {  # the j-th removal of an rsID goes to its j-th still-unaccounted copy
  r <- rm_seq[nth == j]
  todo <- e[observed == "unaccounted"][, first := !duplicated(rsid)][first == TRUE]
  hit <- merge(todo[, .(rsid, copy)], r, by = "rsid")
  e[hit, on = .(rsid, copy), `:=`(observed = "removed", obs_step = i.step, obs_reason = i.reason)]
}
# Kept under another rsID: dbSNP 157 can name a GRCh38 position differently from the upload. Such a row is found by
# its unchanged |BETA| and SE among the QC'd rows whose rsID isn't in the upload (only one-to-one matches count).
spare <- qc[!rsid %in% e$rsid]
todo <- e[observed == "unaccounted"]
if (nrow(spare) && nrow(todo)) {
  fp <- function(b, se) paste(signif(abs(b), 6), signif(se, 6))
  m <- match(fp(todo$beta, todo$se), fp(spare$beta, spare$se))
  once <- !is.na(m) & !(m %in% m[duplicated(m)]) & !duplicated(fp(spare$beta, spare$se))[m]
  e[todo[once], on = .(rsid, copy), `:=`(observed = "kept", qc_row = spare$qc_row[m[once]], renamed = TRUE)]
}
e[, liftover_loss := observed == "removed" & obs_step == "harmonise_liftover" &
     grepl("could not be lifted|lifted to another chromosome|two ALT alleles after the liftover", obs_reason)]
e[, as_expected := observed == expect & (expect == "kept" | (obs_step == step & startsWith(obs_reason, reason)))]

# Kept rows' values, aligned to the expected effect allele (harmonisation switches alleles; the liftover may switch
# or complement them). For A/T and C/G SNPs a switch and a complement look the same, so EAF decides (the filter
# removed those with MAF above 0.4, so EAF and 1 - EAF are far apart).
COMP <- c(A = "T", C = "G", G = "C", T = "A")
comp <- function(x) unname(COMP[x])
site <- qc[e[observed == "kept", qc_row]]
setnames(site, paste0(names(site), ".site"))
k <- cbind(e[observed == "kept"], site)
k[, sign := fcase(ea.site == ea & oa.site == oa & ea != comp(oa), 1, ea.site == oa & oa.site == ea & ea != comp(oa), -1,
                  ea.site == comp(ea) & oa.site == comp(oa) & ea != comp(oa), 1, ea.site == comp(oa) & oa.site == comp(ea) & ea != comp(oa), -1,
                  ea == comp(oa), fifelse(abs(eaf.site - eaf) < abs(1 - eaf.site - eaf), 1, -1), default = NA_real_)]
k[, `:=`(beta_aligned = sign * beta.site, eaf_aligned = fifelse(sign == 1, eaf.site, 1 - eaf.site))]
k[, values_ok := !is.na(sign) & abs(beta_aligned - beta) <= th$beta_se_max_abs_diff & abs(se.site - se) <= th$beta_se_max_abs_diff &
     abs(eaf_aligned - eaf) <= th$eaf_max_abs_diff & n.site == n]
e[k, on = .(rsid, copy), values_ok := i.values_ok]
e[observed == "kept" & is.na(values_ok), values_ok := FALSE]

checks <- list()
add <- function(...) checks[[length(checks) + 1]] <<- check(...)
PG <- "Planted problems (each in its own rows): removed by the expected step and rule, or kept with the right values"
AG <- "Every other row accounted for"
for (p in t$cfg$planted$problems) {
  if (p$id == "header_line") {
    got <- parse_rm[rsid == "rsid"]
    add("planted_header_line", sprintf("%s: rows not as expected", p$label),
        as.integer(nrow(got) != 1 || got$reason[1] != "repeated header line"), th$planted_rows_wrong, "equal", PG)
    next
  }
  rows <- e[problem == p$id]
  wrong <- rows[!as_expected | (observed == "kept" & !values_ok), .N]
  how <- c(removed = "removed", kept = "kept", one_kept = "one copy kept")[[p$expect]]
  add(paste0("planted_", p$id), sprintf("%s (%d rows): not %s as expected", p$label, nrow(rows), how), wrong,
      th$planted_rows_wrong, "equal", PG)
}
un <- e[problem == ""]
add("unplanted_losses", "Unplanted rows lost other than by the rules or the liftover",
    un[!as_expected & !(expect == "kept" & liftover_loss) & observed != "unaccounted", .N], th$unplanted_unexpected_losses, "equal", AG)
add("unplanted_values", "Unplanted rows kept with values that differ from the upload (aligned)",
    un[observed == "kept" & !values_ok, .N], 0, "equal", AG)
add("unaccounted", "Rows neither in the QC'd file nor in any step's removed rows", e[observed == "unaccounted", .N],
    th$rows_unaccounted, "equal", AG)

# Liftover losses must be exactly what the step itself reports, and the per-step counts what is expected.
st <- steps_of(site_file("planted.json"))
hm <- st$harmonise_liftover$metrics
add("liftover_losses", "Rows lost in the liftover: our count minus the step's own (unmapped + another chromosome + two ALT alleles)",
    e[liftover_loss == TRUE, .N] - ((hm$unmapped %||% 0) + (hm$chromosome_changed %||% 0) + (hm$multiallelic %||% 0)), 0, "equal", AG)
for (s in c("parse", "filter")) {
  site_removed <- st[[s]]$rows_before - st[[s]]$rows_after
  add(paste0("step_count_", s), sprintf("Rows removed by %s: site minus expected", s),
      site_removed - expected_steps[step == s, removed], 0, "equal", AG)
}

#=============================================================================
# Part B: whole-file cases
#=============================================================================
CG <- "Whole-file mistakes (one upload each): stopped or named by the expected step"
case_rows <- list()
for (cs in t$cfg$cases) {
  st <- steps_of(site_file("cases", paste0(cs$id, ".json")))
  if (isTRUE(cs$expect$no_gotcha_messages)) {
    msgs <- unlist(lapply(st, messages_of))
    found <- msgs[vapply(msgs, function(m) any(vapply(t$cfg$gotcha_messages, grepl, logical(1), x = m, fixed = TRUE)), logical(1))]
    add("case_control", "Unchanged file: messages naming a mistake (none expected)", length(found), th$control_gotcha_messages, "equal", CG)
    case_rows[[cs$id]] <- data.table(case = cs$id, step = "(all)", verdict = "", caught = length(found) == 0, message = paste(found, collapse = " | "))
    next
  }
  s <- st[[cs$expect$step]]
  verdict <- s$verdict %||% "not run"
  msg <- grep(cs$expect$message, messages_of(s), fixed = TRUE, value = TRUE)
  caught <- verdict %in% unlist(cs$expect$verdict) && length(msg) > 0
  add(paste0("case_", cs$id), sprintf("%s: %s %s, saying \"%s\" (0 = caught)", cs$change, cs$expect$step,
                                      paste(unlist(cs$expect$verdict), collapse = " or "), cs$expect$message),
      as.integer(!caught), 0, "equal", CG)
  case_rows[[cs$id]] <- data.table(case = cs$id, step = cs$expect$step, verdict = verdict, caught = caught, message = if (length(msg)) msg[1] else "")
}
fwrite(rbindlist(case_rows), t$path("reference", "cases_observed.tsv"), sep = "\t")
fwrite(e, t$path("reference", "rows_observed.tsv.gz"), sep = "\t", na = "NA")

site_run <- read_yaml_keep_yn(file.path(t$dir, "site_run.yaml"))
versions <- list(r = R.version.string, data.table = as.character(packageVersion("data.table")),
                 site_pipeline_version = site_run$pipeline_version, site_image_digests = site_run$image_digests)
notes <- c(sprintf("%s rows in the planted file: %s planted, %s kept on the site, %s lost in the liftover",
                   format(nrow(e) + 1L, big.mark = ","), format(e[problem != "", .N] + 1L, big.mark = ","),
                   format(e[observed == "kept", .N], big.mark = ","), format(e[liftover_loss == TRUE, .N], big.mark = ",")),
           sprintf("%d kept rows carry another rsID from dbSNP 157 (matched on |β| and se)", e[renamed == TRUE, .N]),
           sprintf("Per-row outcomes: reference/rows_observed.tsv.gz; whole-file cases: reference/cases_observed.tsv"))
write_result(t, checks, versions, site_run, notes = notes)
write_plot(t, k$beta, k$beta_aligned, rep(1, nrow(k)), labels = c(x = "Uploaded β (planted swaps and strand flips undone)", y = "GWASHub QC'd β (aligned)"))
write_manifest(t)
