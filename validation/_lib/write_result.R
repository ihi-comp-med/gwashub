# result.json (read by the Validation page) and manifest.tsv (sha256 of every large file the result came from).
write_result <- function(t, checks, versions, site_run, notes = character()) {
  status <- if (length(checks) && all(vapply(checks, `[[`, logical(1), "passed"))) "passed" else "failed"
  res <- list(test = t$name, title = t$cfg$title, claim = t$cfg$claim, status = status,
              run_date = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
              versions = versions, site_run = site_run, checks = checks, notes = as.list(notes),
              archive = t$cfg$archive)
  write_json(res, file.path(t$dir, "result.json"), auto_unbox = TRUE, pretty = TRUE, digits = NA, null = "null")
  say("%s: %s (%d of %d checks passed)", t$name, toupper(status),
      sum(vapply(checks, `[[`, logical(1), "passed")), length(checks))
  invisible(res)
}

write_manifest <- function(t) {
  files <- list.files(t$work, recursive = TRUE, full.names = TRUE)
  m <- data.table(path = sub(paste0(t$work, "/"), "", files, fixed = TRUE),
                  bytes = file.size(files), sha256 = vapply(files, sha256, character(1)))
  fwrite(m[order(path)], file.path(t$dir, "manifest.tsv"), sep = "\t")
  say("manifest.tsv: %d files", nrow(m))
}

# Plot data for the page: every variant with p < keep_p in either table, plus a random sample of the rest.
write_plot <- function(t, x, y, p_for_keep, keep_p = 1e-5, sample_n = 5000, labels = c(x = "site", y = "reference")) {
  set.seed(1)
  strong <- which(p_for_keep < keep_p)
  rest <- setdiff(seq_along(x), strong)
  idx <- sort(c(strong, sample(rest, min(sample_n, length(rest)))))
  write_json(list(x_label = jsonlite::unbox(labels[["x"]]), y_label = jsonlite::unbox(labels[["y"]]), x = x[idx], y = y[idx]),
             file.path(t$dir, "plot.json"), digits = 6)
}
