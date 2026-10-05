"""Locus plots and nearest genes for a meta-analysis's lead loci (after _metalib/loci.py).

  region_<locus_id>.json   one per locus: every variant in the locus (± a flank) with -log10 P and its r² with the locus's
                           lead SNP in the chosen 1000 Genomes panel (null when either isn't in the panel), the lead and
                           other signals, and the GENCODE genes in view (genes_b38.tsv.gz, resources/build/genes_gencode.sh)
  lead loci columns        nearest_gene, nearest_pc_gene (closest gene of any type / protein-coding to the lead; 0 bp if
                           inside it) and genes_in_locus (protein-coding genes overlapping the locus), as the HERMES 3 locus
                           tables (define_loci_plink.R)
Without the panel the plots have no r² (grey points); without the genes file, no gene track or gene columns.
"""
import json
import os

import numpy as np
import pandas as pd

from _metalib import loci as L

GENES = "genes_b38.tsv.gz"
MAX_POINTS = 3000  # per locus plot: every variant with P < 0.01 or r² ≥ 0.2, and a sample of the rest
MAX_GENES = 80


def load_genes(resources):
    path = os.path.join(resources or "", GENES)
    if not resources or not os.path.exists(path):
        return None
    return pd.read_csv(path, sep="\t", dtype={"chr": str})


def annotate_genes(lead_loci, genes):
    """nearest_gene, nearest_pc_gene and genes_in_locus for each locus."""
    out = lead_loci.copy()
    for col in ("nearest_gene", "nearest_pc_gene", "genes_in_locus"):
        out[col] = ""
    if genes is None or out.empty:
        return out
    by_chr = {c: g for c, g in genes.groupby("chr")}
    for i, r in out.iterrows():
        g = by_chr.get(str(r["chr"]))
        if g is None:
            continue
        pos = int(r["pos"])
        dist = np.where((g["start"] <= pos) & (g["end"] >= pos), 0, np.minimum((g["start"] - pos).abs(), (g["end"] - pos).abs()))
        out.at[i, "nearest_gene"] = g["gene_name"].iloc[int(np.argmin(dist))]
        pc = g["gene_type"] == "protein_coding"
        if pc.any():
            out.at[i, "nearest_pc_gene"] = g.loc[pc, "gene_name"].iloc[int(np.argmin(dist[pc.to_numpy()]))]
        inside = g[pc & (g["end"] >= int(r["locus_start"])) & (g["start"] <= int(r["locus_end"]))]
        out.at[i, "genes_in_locus"] = ";".join(inside.sort_values("start")["gene_name"])
    return out


def _window(r):
    span = int(r["locus_end"]) - int(r["locus_start"])
    flank = max(50_000, span // 10)
    return max(1, int(r["locus_start"]) - flank), int(r["locus_end"]) + flank


def lead_r2(res, lead_loci, prefix, work):
    """locus_id -> {results row index: r² with the locus lead} from one PLINK 2 call (None when no panel)."""
    if prefix is None or lead_loci.empty:
        return {}
    windows = {int(r["locus_id"]): (str(r["chr"]), *_window(r)) for _, r in lead_loci.iterrows()}
    in_window = pd.Series(False, index=res.index)
    for chrom, a, b in windows.values():
        in_window |= (res["chr"].astype(str) == chrom) & res["pos"].between(a, b)
    sub = res[in_window]
    ids = L.panel_ids(prefix, sub, work, "region_ids").dropna()
    # Each locus lead's panel ID, finding its row in res by chr, pos and alleles.
    lead_ids = {}
    key = lambda c, p, a, b: (str(c), int(p), *sorted((a, b)))
    res_key = {key(c, p, a, b): i for i, c, p, a, b in zip(sub.index, sub["chr"], sub["pos"], sub["ea"], sub["oa"])}
    for _, r in lead_loci.iterrows():
        i = res_key.get(key(r["chr"], r["pos"], r["ea"], r["oa"]))
        if i is not None and i in ids.index:
            lead_ids[int(r["locus_id"])] = ids[i]
    if not lead_ids:
        return {}
    extract, snps = os.path.join(work, "region.extract"), os.path.join(work, "region.leads")
    ids.to_csv(extract, index=False, header=False)
    pd.Series(sorted(set(lead_ids.values()))).to_csv(snps, index=False, header=False)
    kb = max(b - a for _, a, b in windows.values()) // 1000 + 1
    out = L.plink2(["--pfile", prefix, "vzs", "--extract", extract, "--ld-snp-list", snps, "--r2-unphased",
                    "--ld-window-r2", "0", "--ld-window-kb", str(kb), "--ld-window", "999999"], work, "region")
    if not os.path.exists(out + ".vcor"):
        return {}
    ld = pd.read_csv(out + ".vcor", sep="\t")
    pair = {}
    for a, b, r2 in zip(ld["ID_A"], ld["ID_B"], ld["UNPHASED_R2"]):
        pair[(a, b)] = pair[(b, a)] = r2
    result = {}
    for locus_id, lid in lead_ids.items():
        chrom, a, b = windows[locus_id]
        rows = sub[(sub["chr"].astype(str) == chrom) & sub["pos"].between(a, b)]
        # r² for variants in the panel (1 for the lead itself); variants not in the panel are left out (no r²).
        result[locus_id] = {i: (1.0 if ids[i] == lid else pair.get((lid, ids[i]))) for i in rows.index if i in ids.index}
    return result


def write_regions(res, lead_loci, signals, resources, opts, out_dir, work):
    """region_<locus_id>.json for every locus; returns the file names."""
    os.makedirs(work, exist_ok=True)
    prefix = L.panel_prefix(resources, opts.get("ld_panel", "EUR"))
    r2 = lead_r2(res, lead_loci, prefix, work)
    genes = load_genes(resources)
    rng = np.random.default_rng(1)
    names = []
    for _, r in lead_loci.iterrows():
        locus_id = int(r["locus_id"])
        chrom, (a, b) = str(r["chr"]), _window(r)
        rows = res[(res["chr"].astype(str) == chrom) & res["pos"].between(a, b)].copy()
        rows["r2"] = [r2.get(locus_id, {}).get(i) for i in rows.index]
        keep = (rows["p"] < 0.01) | (rows["r2"].fillna(0) >= 0.2)
        rest = rows[~keep]
        if keep.sum() + len(rest) > MAX_POINTS:
            rest = rest.iloc[rng.choice(len(rest), max(0, MAX_POINTS - int(keep.sum())), replace=False)]
        rows = pd.concat([rows[keep], rest]).sort_values("pos")
        sig = signals[signals["locus_id"] == locus_id] if len(signals) else signals
        g = []
        if genes is not None:
            gg = genes[(genes["chr"] == chrom) & (genes["end"] >= a) & (genes["start"] <= b)].copy()
            gg["pc"] = gg["gene_type"] == "protein_coding"
            gg = gg.sort_values(["pc", "end"], ascending=[False, False]).head(MAX_GENES).sort_values("start")
            for x in gg.itertuples():
                exons = [[int(s), int(e)] for s, e in (part.split("-") for part in str(x.exons).split(",") if "-" in part)]
                g.append({"name": x.gene_name, "start": int(x.start), "end": int(x.end), "strand": x.strand,
                          "type": x.gene_type, "exons": exons})
        data = {
            "locus_id": locus_id, "chr": chrom, "start": a, "end": b,
            "locus_start": int(r["locus_start"]), "locus_end": int(r["locus_end"]), "ld_panel": opts.get("ld_panel") if r2 else None,
            "gws": opts.get("gws", 5e-8),
            "lead": {"pos": int(r["pos"]), "rsid": str(r["rsid"]) if "rsid" in r and isinstance(r["rsid"], str) else None,
                     "logp": float(-np.log10(max(float(r["p"]), 1e-300)))},
            "leads": [int(p) for p in sig.loc[sig["is_lead"].astype(bool), "pos"]] if len(sig) else [int(r["pos"])],
            "signals": [int(p) for p in sig["pos"]] if len(sig) else [int(r["pos"])],
            "points": {"pos": rows["pos"].astype(int).tolist(),
                       "logp": (-np.log10(rows["p"].clip(lower=1e-300))).round(3).tolist(),
                       "r2": [None if v is None or pd.isna(v) else round(float(v), 3) for v in rows["r2"]],
                       "rsid": rows["rsid"].fillna("").astype(str).tolist() if "rsid" in rows else []},
            "genes": g,
        }
        name = f"region_{locus_id}.json"
        with open(os.path.join(out_dir, name), "w") as f:
            json.dump(data, f)
        names.append(name)
    return names
