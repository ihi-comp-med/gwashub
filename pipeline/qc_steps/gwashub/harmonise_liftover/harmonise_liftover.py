"""Harmonise and liftover (GWASHub default step). GWASHub format in (uploaded build), GWASHub format out (GRCh38,
effect allele = ALT). Every VCF stays inside this step.

As the HERMES 3 DCM pipeline (hermes3_dcm Snakefile rules quality_control_gwas2vcf, _liftover and _vcf_to_tsv, and the
chromosome check in gwas_qc_report.R; commit ff35109):
  1. gwas2vcf (the edited copy in ./gwas2vcf, run in its Python 3.7 environment) harmonises every variant to the
     reference FASTA of the uploaded build: REF must match, alleles are switched (and BETA/EAF flipped) when the effect
     allele is REF, indels are left-aligned and trimmed. Variants it can't read or harmonise are dropped.
  2. GRCh37 only: bcftools +liftover to GRCh38 (hg19ToHg38 chain, both FASTAs), then sort.
  3. bcftools annotate: rsIDs from dbSNP 157 (GRCh38).
  4. bcftools query back to a table. Removed here: variants lifted to a different chromosome, variants on
     contigs other than 1-22/X/Y, alleles other than A/C/G/T, and duplicates created by the liftover.
Warns (never fails) when fewer than 95% of variants harmonise or lift over. Fails only if no variants are left.

Wrong build declared (the commonest upload mistake): before harmonising, up to 20,000 autosomal SNPs are looked up in
both FASTAs; in the right build nearly every one has one of its alleles as the reference base, in the wrong one about
half. When the other build matches clearly better, the step warns and names it.

Every removed variant is written to removed_rows.tsv.gz with its stage and reason: gwas2vcf's drops from its
--rejected list (a GWASHub addition to the edited copy: data line and reason), the liftover's from bcftools' --reject
file, and the later ones from the masks below. chr_input/pos_input give each one's position in this step's input.
"""
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile

import pandas as pd

from _lib.common import CHROMS_XY, STANDARD, load_params, parse_args, step_params, write_log

HERE = os.path.dirname(os.path.abspath(__file__))
GWAS2VCF = os.path.join(HERE, "gwas2vcf", "main.py")
FASTA = {"GRCh37": "human_g1k_v37.fasta", "GRCh38": "Homo_sapiens_assembly38_nochr.fasta"}
CHAIN = "hg19ToHg38.over.chain.gz"
DBSNP = "dbSNP157_b38_clean.vcf.gz"
DEFAULTS = {"warn_harmonised_fraction": 0.95, "warn_lifted_fraction": 0.95}
FIELDS = ["chr", "pos", "id", "ref", "alt", "es", "se", "lp", "af", "ss", "si", "nc", "pv", "c37", "p37"]
QUERY = "%CHROM\t%POS\t%ID\t%REF\t%ALT\t[%ES\t%SE\t%LP\t%AF\t%SS\t%SI\t%NC\t%PV\t%C37\t%P37]"
REMOVED = ["stage", "reason", "line", "chr_input", "pos_input", "chr", "pos", "ea", "oa", "beta", "se", "p", "eaf"]
# gwas2vcf's closing log lines -> metric names
COUNTERS = {"Total variants": "gwas2vcf_total", "Variants could not be read": "not_read", "Variants harmonised": "harmonised",
            "Variants discarded during harmonisation": "not_harmonised", "Alleles switched": "alleles_switched",
            "Normalised variants": "normalised"}


BUILD_SAMPLE = 20_000
# Run with gwas2vcf's Python (it has pysam, which reads plain and bgzipped FASTAs): stdin "chr<TAB>pos" lines, stdout
# the reference base at each ("" off the contig or for a contig the FASTA lacks).
FETCH = """
import sys, pysam
fa = pysam.FastaFile(sys.argv[1])
size = dict(zip(fa.references, fa.lengths))
for line in sys.stdin:
    c, p = line.split()
    p = int(p)
    print(fa.fetch(c, p - 1, p).upper() if 0 < p <= size.get(c, 0) else "")
"""


class StepFailed(Exception):
    pass


def run(cmd, log_path, **kw):
    """Run a command, appending its output to log_path; raise StepFailed with the log's tail if it fails."""
    with open(log_path, "a") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        proc = subprocess.run(cmd, stdout=kw.pop("stdout", log), stderr=log, **kw)
    if proc.returncode != 0:
        raise StepFailed(f"{os.path.basename(cmd[0])} failed (exit {proc.returncode}):\n" + tail(log_path))
    return proc


def tail(path, n=12):
    with open(path) as f:
        return "".join(f.readlines()[-n:]).strip()


def count_records(vcf):
    out = subprocess.run(["bcftools", "view", "-H", vcf], capture_output=True, check=True)
    return out.stdout.count(b"\n")


def gwas2vcf_json(header, build):
    """gwas2vcf's column JSON for the working file (0-based indexes), as gwas_qc_parse.R wrote it."""
    idx = {c: i for i, c in enumerate(header)}
    j = {"chr_col": idx["chr"], "pos_col": idx["pos"], "ea_col": idx["ea"], "oa_col": idx["oa"], "beta_col": idx["beta"],
         "se_col": idx["se"], "pval_col": idx["p"], "delimiter": "\t", "header": True, "build": build}
    for col, key in (("rsid", "snp_col"), ("eaf", "eaf_col"), ("ncase", "ncase_col"), ("ncontrol", "ncontrol_col"),
                     ("n", "n_col"), ("info", "imp_info_col")):
        if col in idx:
            j[key] = idx[col]
    if build == "GRCh37":  # carried through the liftover to catch variants that move chromosome
        j["chr_build37_col"], j["pos_build37_col"] = idx["chr"], idx["pos"]
    return j


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    art = os.environ.get("GWASHUB_ARTIFACT_DIR") or tempfile.mkdtemp()
    os.makedirs(art, exist_ok=True)
    try:
        metrics, messages, verdict = harmonise(args, params, opts, art)
    except StepFailed as e:
        write_log(args.log, "harmonise_liftover", "fail", {}, [str(e)], artifacts(art))
        return
    write_log(args.log, "harmonise_liftover", verdict, metrics, messages, artifacts(art))


def artifacts(art):
    out = []
    for path, label, kind in (("harmonise_liftover_summary.tsv", "Variants at each stage", "table"),
                              ("removed_rows.tsv.gz", "Removed rows", "table"),
                              ("gwas2vcf.log", "gwas2vcf log", "text"), ("bcftools.log", "bcftools log", "text")):
        if os.path.exists(os.path.join(art, path)):
            out.append({"type": kind, "path": path, "label": label})
    for a in out:
        if a["path"] == "removed_rows.tsv.gz":
            a["rows"] = sum(1 for _ in gzip.open(os.path.join(art, a["path"]), "rt")) - 1
            a["description"] = ("Every removed variant with the stage and reason; line is its row in this step's input "
                                "(gwas2vcf drops only), chr_input/pos_input its position there.")
    return out


def input_rows(path, lines):
    """The rows at these data line numbers (1 = first data row) of the working file, as text."""
    want, rows = set(lines), []
    with open(path) as f:
        header = f.readline().rstrip("\n").split("\t")
        for i, line in enumerate(f, 1):
            if i in want:
                rows.append([i, *line.rstrip("\n").split("\t")])
    return pd.DataFrame(rows, columns=["line", *header], dtype=str)


def vcf_rows(query):
    """bcftools query rows (FIELDS) as the GWASHub columns of REMOVED; chr_input/pos_input from C37/P37 when there.
    A record with two ALT alleles holds one value per ALT (e.g. ".,0.7459"): kept as text, and P left empty."""
    num = lambda col: pd.to_numeric(query[col], errors="coerce")
    return pd.DataFrame({"chr": query["chr"], "pos": query["pos"], "ea": query["alt"], "oa": query["ref"], "beta": query["es"],
                         "se": query["se"], "p": num("pv").fillna(10 ** -num("lp")), "eaf": query["af"],
                         "chr_input": query["c37"].fillna(query["chr"]), "pos_input": query["p37"].fillna(query["pos"])})


def write_removed(parts, art):
    """One gzipped table of every removed variant (nothing written when none were)."""
    parts = [p for p in parts if len(p)]
    if parts:
        pd.concat(parts, ignore_index=True).reindex(columns=REMOVED).to_csv(
            os.path.join(art, "removed_rows.tsv.gz"), sep="\t", index=False, na_rep="NA")


def build_match(path, python37, fastas):
    """{build: share of sampled SNPs whose reference base in that build's FASTA is EA or OA}; {} when too few SNPs."""
    d = pd.read_csv(path, sep="\t", usecols=["chr", "pos", "ea", "oa"], dtype={"chr": str, "ea": str, "oa": str})
    d = d[d["chr"].isin([str(i) for i in range(1, 23)]) & d["ea"].str.len().eq(1) & d["oa"].str.len().eq(1)]
    if len(d) < 1000:
        return {}
    d = d.sample(min(BUILD_SAMPLE, len(d)), random_state=1)
    regions = "".join(f"{c}\t{p}\n" for c, p in zip(d["chr"], d["pos"]))
    out = {}
    for build, fasta in fastas.items():
        if not os.path.exists(fasta):
            continue
        proc = subprocess.run([python37, "-c", FETCH, fasta], input=regions, capture_output=True, text=True)
        if proc.returncode != 0:
            continue
        bases = proc.stdout.split("\n")[:len(d)]
        out[build] = sum(b in (ea, oa) for b, ea, oa in zip(bases, d["ea"], d["oa"])) / len(d)
    return out


def harmonise(args, params, opts, art):
    build = params.get("build")
    if build not in FASTA:
        raise StepFailed(f"Genome build {build!r} isn't supported: GRCh37 or GRCh38.")
    python37 = os.environ.get("GWAS2VCF_PYTHON")
    if not python37:
        raise StepFailed("gwas2vcf's Python environment isn't here (GWAS2VCF_PYTHON): this step runs in the gwashub/vcf image.")
    res = lambda key: os.path.join(args.resources, key)

    with open(args.input) as f:
        header = f.readline().rstrip("\n").split("\t")
    rows_in = sum(1 for _ in open(args.input)) - 1
    tmp = tempfile.mkdtemp(prefix="harmonise_")
    g2v_log, bcf_log = os.path.join(art, "gwas2vcf.log"), os.path.join(art, "bcftools.log")
    open(bcf_log, "w").close()
    threads = str(max(1, min(4, os.cpu_count() or 1)))

    # 0. is the declared build the right one?
    match = build_match(args.input, python37, {b: res(f) for b, f in FASTA.items()})
    other = next(b for b in FASTA if b != build)

    # 1. gwas2vcf: harmonise to the uploaded build's FASTA
    json_path = os.path.join(tmp, "gwas2vcf.json")
    with open(json_path, "w") as f:
        json.dump(gwas2vcf_json(header, build), f)
    vcf = os.path.join(tmp, "gwas.vcf.gz")
    rejected_g2v = os.path.join(tmp, "gwas2vcf_rejected.tsv")
    study = "-".join(re.sub(r"[^A-Za-z0-9]+", "", str(params.get(k) or "")) for k in ("cohort", "phenotype", "ancestry", "sex")).strip("-")
    with open(g2v_log, "w") as log:
        proc = subprocess.run([python37, GWAS2VCF, "--data", args.input, "--json", json_path, "--id", study or "dataset",
                               "--ref", res(FASTA[build]), "--out", vcf, "--rejected", rejected_g2v],
                              cwd=os.path.dirname(GWAS2VCF), stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "TMPDIR": tmp})
    # gwas2vcf reports bad input with sys.exit() (exit code 0) and no output: check for the file, not just the code.
    if proc.returncode != 0 or not os.path.exists(vcf):
        raise StepFailed("gwas2vcf didn't write a VCF:\n" + tail(g2v_log))
    with open(g2v_log) as f:
        text = f.read()
    metrics = {"rows_in": rows_in, "build_in": build, **{f"build_match_{b}": round(v, 4) for b, v in match.items()}}
    for label, key in COUNTERS.items():
        m = re.findall(rf"{label}: (\d+)", text)
        if m:
            metrics[key] = int(m[-1])
    stages = [("input", rows_in), ("harmonised to the FASTA", metrics.get("harmonised"))]
    removed = []  # DataFrames of removed variants (REMOVED columns)
    if os.path.exists(rejected_g2v) and os.path.getsize(rejected_g2v):
        rej = pd.read_csv(rejected_g2v, sep="\t", header=None, names=["line", "reason"], dtype={"line": "Int64", "reason": str})
        rows = input_rows(args.input, rej["line"].dropna().astype(int)).assign(line=lambda d: d["line"].astype(int))
        g = rej.merge(rows, on="line", how="left")
        removed.append(g.assign(stage="gwas2vcf", chr_input=g.get("chr"), pos_input=g.get("pos")))

    # 2. GRCh37 -> GRCh38
    flags = ""
    if build == "GRCh37":
        lifted_raw, rejected = os.path.join(tmp, "lifted.unsorted.bcf"), os.path.join(tmp, "rejected.vcf.gz")
        run(["bcftools", "+liftover", "--no-version", "-Ob", "-o", lifted_raw, vcf, "--",
             "-s", res(FASTA["GRCh37"]), "-f", res(FASTA["GRCh38"]), "-c", res(CHAIN),
             "--reject", rejected, "--reject-type", "z"], bcf_log)
        lifted = os.path.join(tmp, "lifted.vcf.gz")
        run(["bcftools", "sort", "-T", os.path.join(tmp, "sort"), "-Oz", "-o", lifted, lifted_raw], bcf_log)
        metrics["lifted"] = count_records(lifted)
        metrics["unmapped"] = count_records(rejected)
        if metrics["unmapped"]:
            rq = os.path.join(tmp, "rejected.tsv")
            with open(rq, "w") as out:
                run(["bcftools", "query", "-f", QUERY + "\n", rejected], bcf_log, stdout=out)
            r = pd.read_csv(rq, sep="\t", header=None, names=FIELDS, dtype=str, na_values=["."], keep_default_na=False)
            removed.append(vcf_rows(r).assign(stage="liftover", reason="could not be lifted to GRCh38"))
        stages.append(("lifted to GRCh38", metrics["lifted"]))
        vcf, flags = lifted, "\t%INFO/FLIP\t%INFO/SWAP"
    run(["bcftools", "index", "-f", "--csi", vcf], bcf_log)

    # 3. rsIDs from dbSNP (GRCh38)
    annotated = os.path.join(tmp, "annotated.vcf.gz")
    run(["bcftools", "annotate", "-a", res(DBSNP), "-c", "ID", "--threads", threads, "-Oz", "-o", annotated, vcf], bcf_log)

    # 4. back to a table
    table = os.path.join(tmp, "query.tsv")
    with open(table, "w") as out:
        run(["bcftools", "query", "-f", QUERY + flags + "\n", annotated], bcf_log, stdout=out)
    names = FIELDS + (["flip", "swap"] if flags else [])
    q = pd.read_csv(table, sep="\t", header=None, names=names, dtype=str, na_values=["."], keep_default_na=False)
    if flags:
        metrics["strand_flipped"] = int(q["flip"].fillna("0").ne("0").sum())
        metrics["ref_alt_swapped"] = int(q["swap"].fillna("0").ne("0").sum())

    def drop(mask, metric, reason):
        nonlocal q
        metrics[metric] = int(mask.sum())
        if mask.any():
            r = vcf_rows(q[mask])
            removed.append(r.assign(stage="after liftover" if build == "GRCh37" else "after harmonisation",
                                    reason=reason(r) if callable(reason) else reason))
        q = q[~mask]

    drop(q["c37"].notna() & (q["c37"] != q["chr"]), "chromosome_changed",
         lambda r: "lifted to another chromosome (from " + r["chr_input"] + ")")
    drop(~q["chr"].isin(CHROMS_XY), "other_contigs", "on a contig other than 1-22, X, Y")
    # bcftools +liftover makes a record multi-allelic ("reference added") when the GRCh38 base is neither allele:
    # it adds that base as REF and both alleles become ALTs (ALT "A,T")
    metrics["multiallelic"] = int(q["alt"].str.contains(",", na=False).sum())
    drop(~(q["ref"].str.fullmatch("[ACGT]+", na=False) & q["alt"].str.fullmatch("[ACGT]+", na=False)), "invalid_alleles",
         lambda r: r["ea"].fillna("").str.contains(",").map(
             {True: "two ALT alleles after the liftover (the GRCh38 reference base is neither allele)",
              False: "alleles other than A/C/G/T"}))  # e.g. an N from the reference next to an indel
    drop(q.duplicated(["chr", "pos", "ref", "alt"]), "duplicates", "duplicate (same chr:pos:alleles) after liftover"
         if build == "GRCh37" else "duplicate (same chr:pos:alleles) after harmonisation")
    write_removed(removed, art)

    # + 0.0 turns -0.0 (a BETA of 0 negated when gwas2vcf switched the alleles) into 0, so no program reads it as negative
    out = pd.DataFrame({"chr": q["chr"], "pos": q["pos"].astype(int), "ea": q["alt"], "oa": q["ref"],
                        "beta": q["es"].astype(float) + 0.0, "se": q["se"].astype(float),
                        "p": q["pv"].astype(float).fillna(10 ** -q["lp"].astype(float))})
    ss, nc = q["ss"].astype(float), q["nc"].astype(float)
    optional = {"eaf": q["af"].astype(float), "n": ss, "ncase": nc, "ncontrol": ss - nc,
                "rsid": q["id"], "info": q["si"].astype(float)}
    for col in STANDARD:
        if col in optional and col in header:
            out[col] = optional[col]
    for col in ("n", "ncase", "ncontrol"):
        if col in out:
            out[col] = out[col].round().astype("Int64")
    out.to_csv(args.input, sep="\t", index=False, na_rep="NA")

    rows_out = len(out)
    metrics.update(rows_out=rows_out, no_rsid=int(out["rsid"].isna().sum()) if "rsid" in out else None)
    stages += [("same chromosome after liftover", rows_out + metrics["other_contigs"] + metrics["invalid_alleles"] + metrics["duplicates"]),
               ("chromosomes 1-22, X, Y", rows_out + metrics["invalid_alleles"] + metrics["duplicates"]),
               ("alleles A/C/G/T", rows_out + metrics["duplicates"]), ("output (duplicates removed)", rows_out)]
    pd.DataFrame([s for s in stages if s[1] is not None], columns=["stage", "variants"]).to_csv(
        os.path.join(art, "harmonise_liftover_summary.tsv"), sep="\t", index=False)

    harmonised_fraction = (metrics.get("harmonised") or 0) / rows_in if rows_in else 0
    metrics["harmonised_fraction"] = harmonised_fraction
    messages = [f"{metrics.get('harmonised', 0):,} of {rows_in:,} variants harmonised to the {build} reference "
                f"({100 * harmonised_fraction:.2f}%); {metrics.get('alleles_switched', 0):,} had their alleles switched."]
    verdict = "pass"
    if harmonised_fraction < opts["warn_harmonised_fraction"]:
        verdict = "warn"
        messages.append(f"Fewer than {100 * opts['warn_harmonised_fraction']:.0f}% of variants match the {build} reference"
                        + (": check the genome build." if harmonised_fraction < 0.5 else "."))
    if other in match and build in match and match[other] >= 0.9 and match[other] > match[build] + 0.2:
        verdict = "warn"
        messages.append(f"The positions look like {other}, not {build}: {100 * match[other]:.1f}% of sampled SNPs "
                        f"have an allele matching the {other} reference base, against {100 * match[build]:.1f}% for {build}. "
                        f"Change the dataset's build to {other} and re-run.")
    if build == "GRCh37":
        lifted_fraction = metrics["lifted"] / metrics["harmonised"] if metrics.get("harmonised") else 0
        metrics["lifted_fraction"] = lifted_fraction
        messages.append(f"Lifted {metrics['lifted']:,} variants to GRCh38 ({100 * lifted_fraction:.2f}%); "
                        f"{metrics['unmapped']:,} couldn't be lifted, {metrics['strand_flipped']:,} were on the other strand.")
        if lifted_fraction < opts["warn_lifted_fraction"]:
            verdict = "warn"
            messages.append(f"Fewer than {100 * opts['warn_lifted_fraction']:.0f}% of variants lifted over.")
    removed = metrics["chromosome_changed"] + metrics["other_contigs"] + metrics["invalid_alleles"] + metrics["duplicates"]
    if removed:
        messages.append(f"Removed {metrics['chromosome_changed']:,} variants lifted to another chromosome, "
                        f"{metrics['other_contigs']:,} on other contigs, {metrics['invalid_alleles']:,} with alleles other than "
                        f"A/C/G/T" + (f" ({metrics['multiallelic']:,} with two ALT alleles after the liftover: the GRCh38 "
                                      "reference base is neither allele)" if metrics["multiallelic"] else "")
                        + f" and {metrics['duplicates']:,} duplicates.")
    if rows_out == 0:
        verdict = "fail"
        messages.append("No variants left.")
    return metrics, messages, verdict


if __name__ == "__main__":
    sys.exit(main())
