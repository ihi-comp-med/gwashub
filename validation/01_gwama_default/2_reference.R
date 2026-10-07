# Test 1, step 2: GWAMA run by hand on the site's QC'd files (the agreed input to the meta-analysis).
#   Rscript validation/01_gwama_default/2_reference.R
# Needs: local/validation/01_gwama_default/site/<SOURCE>_qc.tsv for each input (dataset panel -> "QC'd file").
# GWAMA runs in the site's own GWAMA image (same release); its inputs are prepared here, not by the site.
source(file.path("validation", "_lib", "common.R"))
t <- test_context("01_gwama_default")
ref <- t$cfg$reference

qc_path <- function(s) {
  p <- t$path("site", paste0(s, c("_qc.tsv", "_qc.tsv.gz")))
  p <- p[file.exists(p)]
  if (!length(p)) stop("Download the QC'd file for ", s, " to ", t$path("site", paste0(s, "_qc.tsv")))
  p[1]
}
# As the site loads each dataset before a meta-analysis: drop rows without beta/se and with se <= 0.
qc <- setNames(lapply(t$cfg$inputs, function(s) {
  d <- fread(qc_path(s), colClasses = list(character = "chr"))
  d <- d[!is.na(beta) & !is.na(se) & se > 0]
  d[, m := marker_id(chr, pos, ea, oa)]
  unique(d, by = "m")
}), t$cfg$inputs)

for (s in names(qc)) {
  d <- qc[[s]]
  fwrite(d[, .(MARKERNAME = m, EA = ea, NEA = oa, BETA = beta, SE = se, N = round(n), EAF = eaf)],
         t$path("reference", paste0(s, ".gwama.tsv")), sep = "\t", na = "NA", quote = FALSE)
}
writeLines(paste0(names(qc), ".gwama.tsv"), t$path("reference", "gwama.in"))
args <- c("run", "--rm", "-v", paste0(t$path("reference"), ":/w"), "-w", "/w", ref$gwama_image,
          "GWAMA", "-i", "gwama.in", "-o", "gwama", unlist(ref$gwama_args))
say("$ docker %s", paste(args, collapse = " "))
status <- system2("docker", args)
if (status != 0) stop("GWAMA failed; see ", t$path("reference", "gwama.log.out"))
say("GWAMA by hand: %s", t$path("reference", "gwama.out"))
