# Test 1, step 1: download the sources, apply the shared size recipe, write the files to upload.
#   Rscript validation/01_gwama_default/1_prepare.R
# Output: local/validation/01_gwama_default/inputs/<SOURCE>.tsv (standard column names, so the site's column mapping
# suggests itself), and upload details to type into the site.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("01_gwama_default")

tables <- setNames(lapply(t$cfg$inputs, function(s) read_source(t, s)), t$cfg$inputs)
chosen <- subset_sources(tables, t$cfg$recipe)

for (s in names(chosen)) {
  d <- chosen[[s]][, intersect(STANDARD, names(chosen[[s]])), with = FALSE]
  path <- t$path("inputs", paste0(s, ".tsv"))
  fwrite(d, path, sep = "\t", na = "NA", quote = FALSE)
  src <- t$sources[[s]]
  say("%-8s %s variants -> %s", s, format(nrow(d), big.mark = ","), path)
  say("         upload as: cohort %s, build %s, ancestry %s, sex Combined, N %s", s, src$build, src$ancestry,
      if (is.null(src$n_fixed)) "from the file" else src$n_fixed)
}
say("Next: upload these to the '%s' project, run QC, then the meta-analysis (see README.md).", t$cfg$site$project)
