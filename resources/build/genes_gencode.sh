#!/usr/bin/env bash
# genes_b38.tsv.gz: genes on GRCh38 from GENCODE release 50 (basic annotation), as used by the HERMES 3 locus plots
# (gencode.v50.basic.annotation.gtf.gz). One row per gene on chromosomes 1-22, X, Y: chr (no "chr"), start, end, strand,
# gene_id, gene_name, gene_type, and the exons of its Ensembl canonical transcript (start-end,...). Used for the gene
# track of meta-analysis locus plots and the nearest genes of each lead locus.
source "$(dirname "$0")/_common.sh"
src=$(fetch https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_50/gencode.v50.basic.annotation.gtf.gz \
      gencode.v50.basic.annotation.gtf.gz md5:11e77cf1d4b78b43f57d608e3f7f2202)
python3 - "$src" "$OUT/genes_b38.tsv.gz" <<'PY'
import gzip, re, sys
src, out = sys.argv[1], sys.argv[2]
keep = {str(i) for i in range(1, 23)} | {"X", "Y"}
attr = re.compile(r'(\w+) "([^"]*)"')
genes, exons = {}, {}
with gzip.open(src, "rt") as f:
    for line in f:
        if line.startswith("#"):
            continue
        chrom, _, feature, start, end, _, strand, _, info = line.rstrip("\n").split("\t")
        chrom = chrom[3:] if chrom.startswith("chr") else chrom
        if chrom not in keep or feature not in ("gene", "exon"):
            continue
        a = dict(attr.findall(info))
        if feature == "gene":
            genes[a["gene_id"]] = [chrom, int(start), int(end), strand, a["gene_id"], a.get("gene_name", ""), a.get("gene_type", "")]
        elif 'tag "Ensembl_canonical"' in info:
            exons.setdefault(a["gene_id"], []).append((int(start), int(end)))
order = lambda c: (int(c) if c.isdigit() else 23 if c == "X" else 24)
with gzip.open(out, "wt") as w:
    w.write("chr\tstart\tend\tstrand\tgene_id\tgene_name\tgene_type\texons\n")
    for g in sorted(genes.values(), key=lambda g: (order(g[0]), g[1])):
        ex = ",".join(f"{s}-{e}" for s, e in sorted(exons.get(g[4], [])))
        w.write("\t".join(map(str, g)) + f"\t{ex}\n")
print(f"{len(genes):,} genes", file=sys.stderr)
PY
record genes_b38.tsv.gz
