"""Lead loci by LD clumping with PLINK 2, as the HERMES 3 analyses (hermes3_dcm scripts/define_loci_plink.R, commit
ff35109; FUMA's scheme):

  1. candidates: autosomes, EAF within [min_maf, 1 - min_maf] (when there is EAF), in at least min_studies datasets
  2. genome-wide significant (P < gws) candidates clumped at r² < iss_r2 → independent significant SNPs (ISS)
  3. ISS clumped at r² < lead_r2 → lead SNPs (no distance limit in either round)
  4. each lead's LD cloud: candidates with P < locus_p and r² ≥ boundary_r2 within ±boundary_kb set the locus bounds;
     the chosen panel first, then the other super-populations' panels for leads with no partner in it
  5. loci closer than merge_kb merged; a locus reported by its strongest lead; loci with no LD cloud get ±min_locus_kb

Variants are matched to the panel by ID chr:pos:ref:alt, trying both allele orders (the panels' IDs are set that way,
see resources/build/kg1000_b38.sh). Genome-wide significant variants not in the panel can't be clumped and are left out,
as in the HERMES 3 analyses; their number is reported.
Without a panel (not uploaded yet), lead loci fall back to a distance window: the strongest variant, then the next
strongest more than boundary_kb away, and so on.
"""
import os
import subprocess

import numpy as np
import pandas as pd

POPS = ("EUR", "AFR", "AMR", "EAS", "SAS")
DEFAULTS = {"gws": 5e-8, "ld_panel": "EUR", "iss_r2": 0.6, "lead_r2": 0.1, "locus_p": 0.05, "boundary_r2": 0.1,
            "boundary_kb": 500, "merge_kb": 250, "min_locus_kb": 250, "min_maf": 0.01, "min_studies": 2}
PANEL_FILES = (".pgen", ".pvar.zst", ".psam")


def panel_prefix(resources, pop):
    """The PLINK 2 file set for a super-population, if all its files were staged."""
    prefix = os.path.join(resources or "", f"1kg_b38_{pop}")
    return prefix if resources and all(os.path.exists(prefix + ext) for ext in PANEL_FILES) else None


def plink2(args, work, name):
    """Run plink2 --out work/name and return that prefix. "No variants left" (nothing in the panel, nothing significant)
    leaves no output files, which callers read as an empty result; any other failure raises."""
    proc = subprocess.run(["plink2", *args, "--out", os.path.join(work, name)], capture_output=True, text=True)
    text = proc.stdout + proc.stderr
    if proc.returncode != 0 and "No variants remaining" not in text and "No significant" not in text:
        raise RuntimeError(f"plink2 {name} failed: {text[-800:]}")
    return os.path.join(work, name)


def panel_ids(prefix, df, work, tag):
    """Each variant's ID in the panel (chr:pos:ref:alt with either allele as ref), or None if it isn't there."""
    fwd = df["chr"].astype(str) + ":" + df["pos"].astype(str) + ":" + df["oa"] + ":" + df["ea"]
    rev = df["chr"].astype(str) + ":" + df["pos"].astype(str) + ":" + df["ea"] + ":" + df["oa"]
    ids_file = os.path.join(work, f"{tag}.ids")
    pd.concat([fwd, rev]).drop_duplicates().to_csv(ids_file, index=False, header=False)
    out = plink2(["--pfile", prefix, "vzs", "--extract", ids_file, "--write-snplist"], work, tag)
    found = set(open(out + ".snplist").read().split()) if os.path.exists(out + ".snplist") else set()
    return pd.Series(np.where(fwd.isin(found), fwd, np.where(rev.isin(found), rev, None)), index=df.index)


def clump(prefix, df, p_threshold, r2, work, tag):
    """PLINK 2 --clump of df (columns id, p) with no distance limit: index variant ID -> member IDs."""
    clump_in = os.path.join(work, f"{tag}.clump_in")
    df[["id", "p"]].rename(columns={"id": "ID", "p": "P"}).to_csv(clump_in, sep="\t", index=False)
    out = plink2(["--pfile", prefix, "vzs", "--clump", clump_in, "--clump-id-field", "ID", "--clump-p-field", "P",
                  "--clump-p1", f"{p_threshold:.2e}", "--clump-p2", f"{p_threshold:.2e}", "--clump-r2", str(r2),
                  "--clump-kb", "99999"], work, tag)
    if not os.path.exists(out + ".clumps"):
        return {}
    c = pd.read_csv(out + ".clumps", sep="\t")
    return {row.ID: [m.split("(")[0] for m in str(row.SP2).split(",") if m not in (".", "")] for row in c.itertuples()}


def ld_clouds(prefix, nominal, leads, r2, kb, work, tag):
    """Lead ID -> (min, max) position of the lead and its LD partners among `nominal` (r² >= r2 within ±kb)."""
    extract, snps = os.path.join(work, f"{tag}.extract"), os.path.join(work, f"{tag}.leads")
    nominal["id"].to_csv(extract, index=False, header=False)
    pd.Series(leads).to_csv(snps, index=False, header=False)
    out = plink2(["--pfile", prefix, "vzs", "--extract", extract, "--ld-snp-list", snps, "--r2-unphased",
                  "--ld-window-r2", str(r2), "--ld-window-kb", str(kb), "--ld-window", "999999"], work, tag)
    if not os.path.exists(out + ".vcor"):
        return {}
    ld = pd.read_csv(out + ".vcor", sep="\t")
    lead_set, clouds = set(leads), {}
    for a_id, a_pos, b_id, b_pos in zip(ld["ID_A"], ld["POS_A"], ld["ID_B"], ld["POS_B"]):
        for lead, lpos, partner in ((a_id, a_pos, b_pos), (b_id, b_pos, a_pos)):
            if lead in lead_set:
                lo, hi = clouds.get(lead, (lpos, lpos))
                clouds[lead] = (min(lo, partner), max(hi, partner))
    return clouds


def merge_intervals(starts, ends, gap):
    """Group numbers for intervals (one chromosome) that overlap or are within `gap` of each other."""
    order = np.argsort(starts)
    groups, group, cur_end = np.empty(len(starts), int), 0, None
    for i in order:
        if cur_end is not None and starts[i] - cur_end > gap:
            group += 1
        groups[i] = group
        cur_end = ends[i] if cur_end is None else max(cur_end, ends[i])
    return groups


def candidates(res, o):
    df = res[res["chr"].astype(str).isin([str(i) for i in range(1, 23)]) & res["p"].notna()].copy()
    if "eaf" in df and df["eaf"].notna().any():
        df = df[df["eaf"].between(o["min_maf"], 1 - o["min_maf"]) | df["eaf"].isna()]
    if "n_studies" in df:
        df = df[df["n_studies"] >= o["min_studies"]]
    return df


def distance_loci(df, o, res):
    """Fallback without a panel: the strongest variant, then the next more than boundary_kb away, and so on."""
    window = o["boundary_kb"] * 1000
    leads = []
    for row in df[df["p"] < o["gws"]].sort_values("p").itertuples():
        if not any(l.chr == row.chr and abs(l.pos - row.pos) < window for l in leads):
            leads.append(row)
    rows = [{"lead_index": r.Index, "locus_start": max(1, int(r.pos) - window), "locus_end": int(r.pos) + window,
             "n_leads": 1, "n_signals": 1, "leads": _label(res, r.Index), "ld_panel": ""} for r in leads]
    return rows


def define_loci(res, opts, resources, work):
    """Lead loci of a meta-analysis. Returns (loci, signals, info):
      loci     one row per locus: locus_id, its strongest lead's results row, locus_start, locus_end, locus_kb, n_leads,
               n_signals, leads, ld_panel, expanded
      signals  one row per independent significant SNP in a locus: locus_id, its results row, lead_snp (the lead it
               clumped under), is_lead, is_locus_lead
      info     counts for the summary"""
    o = {**DEFAULTS, **{k: v for k, v in (opts or {}).items() if k in DEFAULTS}}
    os.makedirs(work, exist_ok=True)
    df = candidates(res, o)
    gws = df[df["p"] < o["gws"]]
    info = {"loci_method": "clumping", "ld_panel": o["ld_panel"], "n_gws_candidates": int(len(gws))}
    prefix = panel_prefix(resources, o["ld_panel"])
    if prefix is None:
        info["loci_method"] = "distance"
        return _tables(res, distance_loci(df, o, res), o) + (info,)
    if gws.empty:
        return _tables(res, [], o) + (info,)

    df = df.assign(id=panel_ids(prefix, df, work, "ids"))
    gws = df[(df["p"] < o["gws"]) & df["id"].notna()]
    info["n_gws_not_in_panel"] = int((df["p"] < o["gws"]).sum() - len(gws))
    iss = clump(prefix, gws, o["gws"], o["iss_r2"], work, "r1_iss")
    iss_df = gws[gws["id"].isin(iss)]
    leads = clump(prefix, iss_df, o["gws"], o["lead_r2"], work, "r2_leads")
    info["n_iss"], info["n_lead_snps"] = len(iss_df), len(leads)
    if not leads:
        return _tables(res, [], o) + (info,)

    # LD clouds: the chosen panel first, then the others for leads with no partner in it.
    lead_rows = df[df["id"].isin(leads)]
    pending = dict(zip(lead_rows.index, lead_rows["pos"].astype(int)))
    bounds, panel_of = {}, {}
    nominal = df[df["p"] < o["locus_p"]]
    for pop in (o["ld_panel"], *[p for p in POPS if p != o["ld_panel"]]):
        pre = panel_prefix(resources, pop)
        if not pending or pre is None:
            continue
        ids = panel_ids(pre, nominal, work, f"ids_{pop}") if pre != prefix else nominal["id"]
        nom = nominal.assign(id=ids).dropna(subset=["id"])
        want = nom.loc[nom.index.intersection(list(pending)), "id"]
        if want.empty:
            continue
        clouds = ld_clouds(pre, nom, list(want), o["boundary_r2"], o["boundary_kb"], work, f"cloud_{pop}")
        for idx, vid in want.items():
            if vid in clouds and clouds[vid][0] != clouds[vid][1]:
                bounds[idx], panel_of[idx] = clouds[vid], pop
                pending.pop(idx)
    for idx, pos in pending.items():
        bounds[idx], panel_of[idx] = (pos, pos), ""

    # Merge loci within merge_kb; report each by its strongest lead.
    lead_rows = lead_rows.assign(start=[bounds[i][0] for i in lead_rows.index], end=[bounds[i][1] for i in lead_rows.index])
    rows = []
    for chrom, g in lead_rows.groupby(lead_rows["chr"].astype(str)):
        groups = merge_intervals(g["start"].to_numpy(), g["end"].to_numpy(), o["merge_kb"] * 1000)
        for _, m in g.groupby(groups):
            top = m["p"].idxmin()
            start, end = int(m["start"].min()), int(m["end"].max())
            # Each independent signal in the locus and the lead SNP it clumped under (a lead is its own signal). As the
            # HERMES 3 analyses, signals outside the locus bounds (pulled in by weak long-range LD) are left out.
            lead_of = {}
            for li, vid in zip(m.index, m["id"]):
                for member in [vid, *leads.get(vid, [])]:
                    lead_of.setdefault(member, li)
            signals = iss_df[iss_df["id"].isin(list(lead_of)) & iss_df["pos"].between(start, end)]
            rows.append({"lead_index": top, "locus_start": start, "locus_end": end, "n_leads": len(m),
                         "n_signals": int(len(signals)), "leads": ";".join(_label(res, i) for i in m.sort_values("p").index),
                         "ld_panel": panel_of[top],
                         "_signals": {i: lead_of[vid] for i, vid in zip(signals.index, signals["id"])},
                         "_leads": list(m.index)})
    return _tables(res, rows, o) + (info,)


def _label(res, i):
    r = res.loc[i]
    rsid = r.get("rsid") if "rsid" in res else None
    return str(rsid) if isinstance(rsid, str) and rsid else f"{r['chr']}:{int(r['pos'])}"


def _tables(res, rows, o):
    """The loci and signals tables from the per-locus rows (see define_loci). Loci are numbered by P (locus 1 = the
    strongest); without clumping (distance fallback) each locus's only signal is its lead."""
    cols = ["locus_id", *res.columns, "locus_start", "locus_end", "locus_kb", "n_leads", "n_signals", "leads", "ld_panel", "expanded"]
    sig_cols = ["locus_id", *res.columns, "lead_snp", "is_lead", "is_locus_lead"]
    if not rows:
        return pd.DataFrame(columns=cols), pd.DataFrame(columns=sig_cols)
    rows = sorted(rows, key=lambda r: res.loc[r["lead_index"], "p"])
    loci = pd.concat([res.loc[[r["lead_index"] for r in rows]].reset_index(drop=True),
                      pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_") and k != "lead_index"} for r in rows])], axis=1)
    loci.insert(0, "locus_id", range(1, len(loci) + 1))
    half = o["min_locus_kb"] * 1000
    loci["expanded"] = loci["locus_start"] == loci["locus_end"]  # no LD cloud: a window around the lead
    loci.loc[loci["expanded"], "locus_start"] = (loci.loc[loci["expanded"], "pos"].astype(int) - half).clip(lower=1)
    loci.loc[loci["expanded"], "locus_end"] = loci.loc[loci["expanded"], "pos"].astype(int) + half
    loci["locus_kb"] = ((loci["locus_end"] - loci["locus_start"]) / 1000).round(1)

    parts = []
    for locus_id, r in enumerate(rows, 1):
        signal_of = r.get("_signals") or {r["lead_index"]: r["lead_index"]}
        leads = set(r.get("_leads") or [r["lead_index"]])
        sig = res.loc[list(signal_of)].copy()
        sig.insert(0, "locus_id", locus_id)
        sig["lead_snp"] = [_label(res, signal_of[i]) for i in sig.index]
        sig["is_lead"] = [i in leads for i in sig.index]
        sig["is_locus_lead"] = [i == r["lead_index"] for i in sig.index]
        parts.append(sig.sort_values("p"))
    signals = pd.concat(parts, ignore_index=True)
    return loci[cols], signals[sig_cols]
