# Shared helpers for the validation tests. Each test script starts with:
#   source(file.path("validation", "_lib", "common.R")); t <- test_context("01_gwama_default")
suppressPackageStartupMessages({ library(data.table); library(yaml); library(jsonlite) })

# Columns of the files uploaded to the site (and of the site's QC'd files, which add ncase, ncontrol, info).
STANDARD <- c("chr", "pos", "ea", "oa", "beta", "se", "eaf", "p", "n", "rsid")

# YAML, but with a bare y / n left as text: R's yaml package follows YAML 1.1, where they mean true / false, which turns
# a `n:` column key into FALSE. true / false / yes / no are still booleans.
read_yaml_keep_yn <- function(path) {
  read_yaml(path, handlers = list(
    "bool#yes" = function(x) if (x %in% c("y", "Y")) x else TRUE,
    "bool#no" = function(x) if (x %in% c("n", "N")) x else FALSE))
}

repo_root <- function() {
  d <- normalizePath(getwd())
  while (!file.exists(file.path(d, "validation", "sources.yaml"))) {
    if (dirname(d) == d) stop("Run from inside the GWASHub repository")
    d <- dirname(d)
  }
  d
}

# Paths and settings for one test: committed files in validation/<test>/, large files in local/validation/<test>/.
test_context <- function(test) {
  root <- repo_root()
  dir <- file.path(root, "validation", test)
  work <- file.path(root, "local", "validation", test)
  for (sub in c("sources", "inputs", "site", "reference")) dir.create(file.path(work, sub), recursive = TRUE, showWarnings = FALSE)
  list(name = test, root = root, dir = dir, work = work,
       cfg = read_yaml_keep_yn(file.path(dir, "test.yaml")),
       sources = read_yaml_keep_yn(file.path(root, "validation", "sources.yaml"))$sources,
       path = function(...) file.path(work, ...))
}

# chr:pos:<alleles in alphabetical order>: the same variant whichever allele a file calls the effect allele
# (as the site's meta-analysis matches variants, pipeline/analyses/meta_analysis/_metalib/common.py marker_id).
marker_id <- function(chr, pos, a, b) paste(chr, as.integer(pos), pmin(a, b), pmax(a, b), sep = ":")

sha256 <- function(path) {
  out <- system2("shasum", c("-a", "256", shQuote(path)), stdout = TRUE)
  sub(" .*", "", out)
}

say <- function(...) cat(sprintf(...), "\n")

`%||%` <- function(a, b) if (is.null(a)) b else a

# Load the other shared files.
for (f in c("get_sources.R", "subset.R", "compare.R", "write_result.R", "gwama.R")) source(file.path(repo_root(), "validation", "_lib", f))
