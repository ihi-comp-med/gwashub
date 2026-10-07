# Compare two meta-analysis result tables. Each needs chr, pos, ea, oa and any of beta, se, p, n_studies, ...
# Rows are matched on chr:pos:<sorted alleles>; where the effect alleles differ, b's beta is negated (and its eaf,
# if present, replaced by 1 - eaf) so both describe the same allele.
align <- function(a, b) {
  a <- copy(a)[, m := marker_id(chr, pos, ea, oa)]
  b <- copy(b)[, m := marker_id(chr, pos, ea, oa)]
  j <- merge(a, b, by = "m", suffixes = c(".a", ".b"))
  flip <- j$ea.a != j$ea.b
  if ("beta.b" %in% names(j)) j[flip, beta.b := -beta.b]
  if ("z.b" %in% names(j)) j[flip, z.b := -z.b]
  if ("eaf.b" %in% names(j)) j[flip, eaf.b := 1 - eaf.b]
  list(joined = j, only_a = setdiff(a$m, b$m), only_b = setdiff(b$m, a$m), flipped = sum(flip))
}

max_abs_diff <- function(x, y) max(abs(x - y), na.rm = TRUE)

max_rel_diff <- function(x, y, floor = 1e-300) {
  ok <- !is.na(x) & !is.na(y) & x > floor & y > floor
  max(abs(x[ok] - y[ok]) / pmax(x[ok], y[ok]))
}

neglog10_diff <- function(x, y) max(abs(-log10(x) + log10(y)), na.rm = TRUE)

# One check for result.json: observed value against a threshold (direction "max": observed must be <= threshold;
# "equal": observed must equal threshold).
check <- function(id, label, observed, threshold, direction = c("max", "equal")) {
  direction <- match.arg(direction)
  passed <- if (direction == "max") isTRUE(observed <= threshold) else isTRUE(all.equal(observed, threshold))
  list(id = id, label = label, observed = observed, threshold = threshold, direction = direction, passed = passed)
}
