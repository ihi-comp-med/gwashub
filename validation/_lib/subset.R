# The shared size recipe (docs/VALIDATION_PLAN.md): autosomal SNPs with a result (beta, se, P) present in every source
# (matched on rsID, as the sources are on different builds), then every such variant within window_kb of the HF lead
# SNPs, plus a seeded random sample of the rest. Each source keeps its own build and columns; only the rows are chosen.
subset_sources <- function(tables, recipe) {
  keep_snps <- function(d) d[chr %in% as.character(1:22) & nchar(ea) == 1 & nchar(oa) == 1 & grepl("^rs[0-9]+$", rsid) &
                               !is.na(beta) & !is.na(se) & !is.na(p)]  # no result here (e.g. not tested in this population)
  tables <- lapply(tables, function(d) unique(keep_snps(d), by = "rsid"))
  shared <- Reduce(intersect, lapply(tables, `[[`, "rsid"))
  say("Variants (SNPs with rsIDs) in all %d sources: %s", length(tables), format(length(shared), big.mark = ","))

  # Windows around the lead SNPs, on the first source's build (the leads' positions come from that file).
  first <- tables[[1]][rsid %in% shared]
  leads <- first[rsid %in% recipe$lead_snps]
  if (nrow(leads) < length(recipe$lead_snps))
    warning("Lead SNPs not found in every source: ", paste(setdiff(recipe$lead_snps, leads$rsid), collapse = ", "))
  w <- recipe$window_kb * 1000
  in_window <- first[, any_lead := FALSE]
  for (i in seq_len(nrow(leads))) in_window[chr == leads$chr[i] & abs(pos - leads$pos[i]) <= w, any_lead := TRUE]
  window_ids <- in_window[any_lead == TRUE, rsid]

  set.seed(recipe$seed)
  rest <- setdiff(shared, window_ids)
  chosen <- c(window_ids, sample(rest, min(recipe$random_n, length(rest))))
  say("Chosen: %s in the lead windows + %s at random = %s",
      format(length(window_ids), big.mark = ","), format(length(chosen) - length(window_ids), big.mark = ","),
      format(length(chosen), big.mark = ","))
  lapply(tables, function(d) d[rsid %in% chosen][order(as.integer(chr), pos)])
}
