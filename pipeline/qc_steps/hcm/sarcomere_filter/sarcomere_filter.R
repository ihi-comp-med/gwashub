# Sarcomere-variant filter (HCM consortium step, R).
# Removes variants within the regions listed in sarcomere_genes_grch38.bed (from --resources).
suppressPackageStartupMessages({ library(data.table); library(jsonlite) })

args <- commandArgs(trailingOnly = TRUE)
opt <- setNames(as.list(args[c(FALSE, TRUE)]), sub("^--", "", args[c(TRUE, FALSE)]))

dt <- fread(opt$input, colClasses = list(character = "chr"))
genes <- fread(file.path(opt$resources, "sarcomere_genes_grch38.bed"),
               col.names = c("chr", "start", "end", "gene"), colClasses = list(character = "chr"))
genes[, chr := sub("^chr", "", chr)]

before <- nrow(dt)
hit <- rep(FALSE, before)
for (i in seq_len(nrow(genes))) {
  g <- genes[i]
  hit <- hit | (dt$chr == g$chr & dt$pos >= g$start & dt$pos <= g$end)
}
dt <- dt[!hit]
fwrite(dt, opt$input, sep = "\t", na = "NA")

write_json(list(
  step = "sarcomere_filter.R",
  verdict = "pass",
  metrics = list(rows_removed = before - nrow(dt), regions = nrow(genes)),
  messages = list(sprintf("Removed %d variants in %d sarcomere regions.", before - nrow(dt), nrow(genes))),
  artifacts = list()
), opt$log, auto_unbox = TRUE)
