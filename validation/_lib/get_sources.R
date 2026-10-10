# Download a file into local/validation/<test>/sources/ (once) and check its md5. Returns the local path.
# A file whose md5 doesn't match is an error; with no md5 given, the file's md5 is printed to add.
fetch <- function(t, url, md5 = "", label = basename(url)) {
  if (!nzchar(url %||% "")) stop("Fill in the url (and md5) for ", label, " in validation/sources.yaml first")
  dest <- t$path("sources", basename(sub("\\?.*", "", url)))
  if (!file.exists(dest)) {
    say("Downloading %s ...", label)
    # R stops downloads after 60 s by default (too short for GB files), and a cut-off download is only a warning
    # ("downloaded length ... != reported length"), so any warning counts as a failure. A partial download stays in
    # .part, so it is never mistaken for the whole file.
    old <- options(timeout = max(7200, getOption("timeout")))
    on.exit(options(old), add = TRUE)
    part <- paste0(dest, ".part")
    status <- tryCatch(download.file(url, part, mode = "wb", quiet = TRUE),
                       warning = function(w) { say("%s: %s", label, conditionMessage(w)); 1 }, error = function(e) 1)
    if (status != 0) stop("Download failed or was cut off: ", url, " (partial file: ", part, ")")
    file.rename(part, dest)
  }
  got <- unname(tools::md5sum(dest))
  if (nzchar(md5 %||% "")) {
    if (got != md5) stop(sprintf("%s: md5 %s, expected %s (delete %s to download again)", label, got, md5, dest))
  } else {
    say("%s: no md5 in sources.yaml; this file's md5 is %s (add it)", label, got)
  }
  dest
}

get_source <- function(t, name) {
  s <- t$sources[[name]]
  if (is.null(s)) stop("No source ", name, " in validation/sources.yaml")
  fetch(t, s$url, s$md5, name)
}

# fread for plain, gzip and bgzip files alike (fread doesn't recognise the .bgz extension).
read_any <- function(path, ...) {
  if (grepl("\\.(gz|bgz)$", path)) fread(cmd = paste("gzip -dc", shQuote(path)), showProgress = FALSE, ...)
  else fread(path, showProgress = FALSE, ...)
}

# Read a source into the standard columns, using its `columns` mapping from sources.yaml.
read_source <- function(t, name) {
  s <- t$sources[[name]]
  m <- s$columns
  if (!length(m)) stop("Fill in `columns` for ", name, " in validation/sources.yaml (see its header)")
  extra <- c(s$variant_column$column, unlist(s$eaf_from_minor))
  d <- read_any(get_source(t, name), sep = s$sep %||% "auto", select = unique(c(unname(unlist(m)), extra)))
  missing <- setdiff(c(unlist(m), extra), names(d))
  if (length(missing)) stop(name, ": columns not in the file: ", paste(missing, collapse = ", "))
  out <- d[, unlist(m), with = FALSE]
  setnames(out, unlist(m), names(m))
  # chr, pos and alleles packed in one column, e.g. Neale lab's variant = chr:pos:ref:alt (parts names them in order)
  if (!is.null(v <- s$variant_column)) {
    out[, (unlist(v$parts)) := tstrsplit(d[[v$column]], v$sep %||% ":", fixed = TRUE)]
    out[, pos := as.integer(pos)]
  }
  # EAF from a minor allele and its frequency (Neale lab: minor_allele, minor_AF)
  if (!is.null(f <- s$eaf_from_minor)) {
    out[, eaf := fifelse(toupper(d[[f$allele]]) == toupper(ea), d[[f$freq]], 1 - d[[f$freq]])]
  }
  if (!"n" %in% names(out)) {
    if (is.null(s$n_fixed)) stop(name, ": no n column; set n_fixed in sources.yaml")
    out[, n := s$n_fixed]
  }
  out[, chr := sub("^chr", "", as.character(chr))]
  out[, `:=`(ea = toupper(ea), oa = toupper(oa))]
  if (isTRUE(s$p_neglog10)) out[, p := 10^-p]
  if ("rsid" %in% names(out)) out[, rsid := sub(",.*", "", rsid)]  # FinnGen lists several, comma-separated
  if (!is.null(s$rsid_lookup)) out <- add_rsids(t, name, out)
  out[]
}

# rsIDs from another file (sources.yaml rsid_lookup), joined on chr, pos and the two alleles in either order.
add_rsids <- function(t, name, d) {
  lk <- t$sources[[name]]$rsid_lookup
  m <- lk$columns
  look <- read_any(fetch(t, lk$url, lk$md5, paste(name, "rsID lookup")), select = unname(unlist(m[c("chr", "pos", "ref", "alt", "rsid")])),
                   colClasses = list(character = m$chr))
  setnames(look, unlist(m[c("chr", "pos", "ref", "alt", "rsid")]), c("chr", "pos", "ref", "alt", "rsid"))
  look[, chr := sub("^chr", "", chr)]
  look <- look[!is.na(rsid) & nzchar(rsid), .(key = marker_id(chr, pos, toupper(ref), toupper(alt)), rsid)]
  look <- unique(look, by = "key")
  d[, key := marker_id(chr, pos, ea, oa)]
  d[look, rsid := i.rsid, on = "key"]
  d[, key := NULL]
  say("%s: rsID found for %s of %s variants", name, format(sum(!is.na(d$rsid)), big.mark = ","), format(nrow(d), big.mark = ","))
  d
}
