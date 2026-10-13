#!/usr/bin/env Rscript
# =================================================================================================================
# sgc/check_build: is the dataset on the genome build declared at upload? GWASHub code for the Skin Genetics
# Consortium's pipeline: sgc/qc_munge gives qc_munge.R the declared build (rather than letting MungeSumstats infer it),
# so this step checks that build first, and fails the run when the data are on the other one.
#
# Contract
# --------
# The parameters are in step.yaml in this folder. The contract every QC step follows is in CONTRIBUTING.md, section 2.
#
#   Rscript check_build.R --input working.tsv --resources DIR --params params.json --log step_log.json
#
# Inputs
#   --input    GWASHub format (after gwashub/parse), in the build declared at upload. Read only.
#   --params   JSON. Read here: build (GRCh37 or GRCh38), and under "step": sampled_snps, min_match, min_gap.
#
# How
#   With rsIDs: MungeSumstats::get_genome_builds, the way qc_munge.R infers a build when it isn't given one (rsID,
#   chromosome and position matched against dbSNP 155 on each build, from a sample of sampled_snps rsIDs).
#   Without rsIDs (or too few for that): sampled_snps single-base variants on chromosomes 1-22; on each build, the share
#   whose reference base (BSgenome GRCh37 hs37d5 and NCBI GRCh38) is one of the two alleles. On the right build nearly
#   all are; on the wrong one about half are, by chance.
#
# Outputs
#   --input    Unchanged.
#   --log      {"step": "check_build", "verdict", "metrics", "messages"}. Metrics: declared, inferred, method, and for the
#              allele method match_GRCh37 and match_GRCh38.
#
# Verdict
#   fail   the data are on the other build
#   warn   the build couldn't be told (allele method: neither build matches at least min_match, or the two are closer
#          than min_gap)
#   pass   the data are on the declared build
# =================================================================================================================
suppressPackageStartupMessages({ library(data.table); library(jsonlite) })

DEFAULTS <- list(sampled_snps = 10000, min_match = 0.8, min_gap = 0.2)
args <- commandArgs(trailingOnly = TRUE)
opt <- setNames(as.list(args[c(FALSE, TRUE)]), sub("^--", "", args[c(TRUE, FALSE)]))
params <- fromJSON(opt$params, simplifyVector = FALSE)
sp <- modifyList(DEFAULTS, params$step %||% list())
write_log <- function(verdict, metrics, messages) {
  write_json(list(step = "check_build", verdict = verdict, metrics = metrics, messages = as.list(messages)),
             opt$log, auto_unbox = TRUE, digits = NA, null = "null")
}
declared <- params$build %||% NA_character_
if (!declared %in% c("GRCh37", "GRCh38")) {
  write_log("fail", list(declared = declared), sprintf("The declared build (%s) isn't GRCh37 or GRCh38.", declared))
  quit(status = 0)
}

gwas <- fread(opt$input, sep = "\t", na.strings = "NA", colClasses = list(character = c("chr", "ea", "oa")))
set.seed(1)
inferred <- NA_character_
method <- NA_character_
metrics <- list(declared = declared)

# 1. As MungeSumstats infers it (qc_munge.R's way), when there are rsIDs to do it with.
if ("rsid" %in% names(gwas) && sum(grepl("^rs", gwas$rsid)) >= min(sp$sampled_snps, 0.5 * nrow(gwas))) {
  message("Inferring the build with MungeSumstats::get_genome_builds (rsIDs against dbSNP 155).")
  dt <- gwas[grepl("^rs", rsid), .(SNP = rsid, CHR = chr, BP = pos, A1 = oa, A2 = ea)]
  got <- tryCatch(MungeSumstats::get_genome_builds(sumstats_list = list(dataset = dt), sampled_snps = sp$sampled_snps,
                                                   dbSNP = 155, names_from_paths = FALSE),
                  error = function(e) { message("MungeSumstats couldn't infer it: ", conditionMessage(e)); NULL })
  if (!is.null(got)) {
    b <- toupper(as.character(unlist(got))[1])
    inferred <- c(GRCH37 = "GRCh37", GRCH38 = "GRCh38")[[b]] %||% NA_character_
    method <- "MungeSumstats::get_genome_builds (rsIDs, dbSNP 155)"
  }
}

# 2. Otherwise: reference bases of both builds against the alleles.
if (is.na(inferred)) {
  message("Comparing alleles with the reference genome of each build.")
  snv <- gwas[nchar(ea) == 1 & nchar(oa) == 1 & chr %in% as.character(1:22), .(chr, pos, ea, oa)]
  if (nrow(snv) > sp$sampled_snps) snv <- snv[sample(.N, sp$sampled_snps)]
  share <- function(genome) {
    len <- GenomeInfoDb::seqlengths(genome)
    s <- snv[chr %in% names(len) & pos <= len[chr]]
    if (!nrow(s)) return(NA_real_)
    ref <- as.character(BSgenome::getSeq(genome, GenomicRanges::GRanges(s$chr, IRanges::IRanges(s$pos, width = 1))))
    mean(ref == s$ea | ref == s$oa)
  }
  m37 <- share(BSgenome.Hsapiens.1000genomes.hs37d5::BSgenome.Hsapiens.1000genomes.hs37d5)
  m38 <- share(BSgenome.Hsapiens.NCBI.GRCh38::BSgenome.Hsapiens.NCBI.GRCh38)
  metrics$match_GRCh37 <- round(m37, 4)
  metrics$match_GRCh38 <- round(m38, 4)
  metrics$snvs_checked <- nrow(snv)
  method <- "reference bases of both builds against the alleles"
  best <- max(m37, m38, na.rm = TRUE)
  if (is.finite(best) && best >= sp$min_match && abs(m37 - m38) >= sp$min_gap) {
    inferred <- if (m37 > m38) "GRCh37" else "GRCh38"
  }
}

metrics$inferred <- inferred
metrics$method <- method
detail <- if (!is.null(metrics$match_GRCh37))
  sprintf(" (%.1f%% of %s single-base variants match GRCh37's reference, %.1f%% GRCh38's)", 100 * metrics$match_GRCh37,
          format(metrics$snvs_checked, big.mark = ","), 100 * metrics$match_GRCh38) else " (MungeSumstats, from rsIDs)"
if (is.na(inferred)) {
  write_log("warn", metrics, sprintf("Couldn't tell which build the data are on%s; declared %s.", detail, declared))
} else if (inferred != declared) {
  write_log("fail", metrics, sprintf("The data are on %s, not %s as declared%s. Correct the build in the dataset's details and re-run QC.",
                                     inferred, declared, detail))
} else {
  write_log("pass", metrics, sprintf("The data are on %s, as declared%s.", inferred, detail))
}
