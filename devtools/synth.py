"""Synthetic genome, reference resources and fake GWAS summary statistics for local development.

Everything here is SYNTHETIC: a made-up GRCh37 -> GRCh38 chain (with gaps and an inverted
segment), a made-up reference panel with per-ancestry allele frequencies, and simulated
summary statistics with shared causal loci. It lets the whole pipeline run offline and
deterministically. Do not use these files for real analyses.

    python -m devtools.synth --out local/synthetic   # resources + the demo datasets
    python -m devtools.synth --out /tmp/x --small  # tiny version (tests)
"""
import argparse
import gzip
import json
import os
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd
from scipy import stats

ANCESTRIES = ["EUR", "AFR", "EAS", "SAS", "AMR"]
# GRCh37 autosome lengths (bp).
CHR_LEN_37 = [249250621, 243199373, 198022430, 191154276, 180915260, 171115067, 159138663,
              146364022, 141213431, 135534747, 135006516, 133851895, 115169878, 107349540,
              102531392, 90354753, 81195210, 78077248, 59128983, 63025520, 48129895, 51304566]
COMP = str.maketrans("ACGT", "TGCA")


# --------------------------------------------------------------------------------------
# Chain: GRCh37 -> GRCh38
# --------------------------------------------------------------------------------------
@dataclass
class Segment:
    chr: str
    t_start: int  # 0-based, GRCh37
    size: int
    q_start: int  # 0-based forward-strand GRCh38 start of this segment
    strand: str  # '+' or '-'


def make_chain(rng, inverted_chroms=("5", "16")):
    """Segments mapping GRCh37 to GRCh38, plus GRCh38 chromosome sizes.

    Each chromosome is cut into 4-9 segments separated by short unmapped GRCh37 gaps;
    GRCh38 gets small insertions between segments, so positions shift. One segment on each
    of `inverted_chroms` is inverted (maps to the minus strand).
    """
    segments, q_sizes = [], {}
    for i, length in enumerate(CHR_LEN_37):
        c = str(i + 1)
        n = int(rng.integers(4, 10))
        cuts = np.sort(rng.choice(np.arange(1, length - 1, 1_000_000), n - 1, replace=False))
        bounds = [0, *cuts.tolist(), length]
        q = int(rng.integers(0, 200_000))
        inv = int(rng.integers(1, n - 1)) if c in inverted_chroms else -1
        for k in range(n):
            gap = int(rng.integers(20_000, 300_000)) if k > 0 else 0
            t0 = bounds[k] + gap
            size = bounds[k + 1] - t0
            if size <= 0:
                continue
            segments.append(Segment(c, t0, size, q, "-" if k == inv else "+"))
            q += size + int(rng.integers(0, 150_000))
        q_sizes[c] = q + int(rng.integers(0, 100_000))
    return segments, q_sizes


def write_chain(path, segments, q_sizes):
    with gzip.open(path, "wt") as f:
        f.write("# SYNTHETIC chain file for GWASHub local development. Not real GRCh37->GRCh38.\n")
        for i, s in enumerate(segments):
            t_size = CHR_LEN_37[int(s.chr) - 1]
            q_size = q_sizes[s.chr]
            if s.strand == "+":
                qs = s.q_start
            else:  # minus-strand coordinates
                qs = q_size - (s.q_start + s.size)
            f.write(f"chain 1000 chr{s.chr} {t_size} + {s.t_start} {s.t_start + s.size} "
                    f"chr{s.chr} {q_size} {s.strand} {qs} {qs + s.size} {i + 1}\n{s.size}\n\n")


def lift(chrom, pos37, segments, q_sizes):
    """Reference implementation used to build the truth table (1-based in/out)."""
    t = pos37 - 1
    for s in segments:
        if s.chr == chrom and s.t_start <= t < s.t_start + s.size:
            off = t - s.t_start
            q = s.q_start + off if s.strand == "+" else s.q_start + s.size - 1 - off
            return q + 1, s.strand
    return None, None


# --------------------------------------------------------------------------------------
# Variants and reference panel
# --------------------------------------------------------------------------------------
def random_alleles(rng, n):
    """Mostly non-palindromic SNPs, ~10% palindromic, ~5% indels."""
    bases = np.array(list("ACGT"))
    ref, alt = [], []
    for _ in range(n):
        u = rng.random()
        if u < 0.05:  # indel
            r = str(rng.choice(bases))
            a = r + "".join(rng.choice(bases, int(rng.integers(1, 4))))
            if rng.random() < 0.5:
                r, a = a, r
        elif u < 0.15:  # palindromic
            r = str(rng.choice(bases))
            a = r.translate(COMP)
        else:
            while True:
                r, a = rng.choice(bases, 2, replace=False)
                if r.translate(COMP) != a:
                    break
            r, a = str(r), str(a)
        ref.append(r)
        alt.append(a)
    return ref, alt


def make_variants(rng, n_variants, segments, q_sizes, n_hotspots=30, per_hotspot=120):
    """Sparse genome-wide variants plus dense clusters ("hotspots") where causal loci go, so
    association peaks have realistic LD towers rather than single points."""
    total = sum(CHR_LEN_37)
    per_chr = [max(50, int(n_variants * L / total)) for L in CHR_LEN_37]
    hot_chr = rng.choice(len(CHR_LEN_37), n_hotspots, p=np.array(CHR_LEN_37) / total)
    rows = []
    for i, n in enumerate(per_chr):
        c = str(i + 1)
        pos = rng.choice(np.arange(10_000, CHR_LEN_37[i] - 10_000), n, replace=False)
        for _ in range(int((hot_chr == i).sum())):
            centre = int(rng.integers(1_000_000, CHR_LEN_37[i] - 1_000_000))
            pos = np.concatenate([pos, centre + rng.integers(-250_000, 250_000, per_hotspot)])
        pos = np.unique(pos)
        for p in pos:
            p38, strand = lift(c, int(p), segments, q_sizes)
            rows.append((c, int(p), p38, strand))
    df = pd.DataFrame(rows, columns=["chr", "pos37", "pos38", "strand37to38"])
    ref, alt = random_alleles(rng, len(df))
    df["ref"], df["alt"] = ref, alt  # on the GRCh38 forward strand
    df["rsid"] = [f"rs{1000000 + i}" for i in range(len(df))]
    # Allele frequencies: ancestral freq + Balding-Nichols drift per ancestry.
    p0 = np.clip(rng.beta(0.7, 0.7, len(df)), 0.02, 0.98)
    df["hotspot"] = False
    for anc, fst in zip(ANCESTRIES, [0.05, 0.12, 0.10, 0.08, 0.06]):
        a = p0 * (1 - fst) / fst
        b = (1 - p0) * (1 - fst) / fst
        df[f"af_{anc}"] = np.clip(rng.beta(a, b), 0.005, 0.995).round(4)
    for c, g in df.groupby("chr"):
        p = np.sort(g["pos37"].to_numpy())
        lo = np.searchsorted(p, g["pos37"].to_numpy() - 50_000)
        hi = np.searchsorted(p, g["pos37"].to_numpy() + 50_000)
        df.loc[g.index, "hotspot"] = (hi - lo) >= 8
    return df


def write_ref_panel(path, variants):
    panel = variants[variants["pos38"].notna()].copy()
    panel["pos"] = panel["pos38"].astype(int)
    panel = panel.sort_values(["chr", "pos"], key=lambda s: s.astype(int) if s.name == "chr" else s)
    cols = ["chr", "pos", "rsid", "ref", "alt"] + [f"af_{a}" for a in ANCESTRIES]
    with gzip.open(path, "wt") as f:
        f.write("# SYNTHETIC reference panel (GRCh38) for GWASHub local development.\n")
        panel[cols].to_csv(f, sep="\t", index=False)


LD_POPULATIONS = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID")


def write_ld_scores(rng, res_dir, variants):
    """Synthetic LD scores per population, in the format the gwashub/ldsc step reads: chr, pos (GRCh38), l2.
    Higher in the hotspots where simulated signal sits, so polygenic signal shows up as slope (as with real
    LD) rather than intercept. Production uses Pan-UK Biobank LD scores."""
    panel = variants[variants["pos38"].notna()]
    hot = panel["hotspot"].to_numpy().astype(float)  # simulated causal variants and their neighbours sit in hotspots
    for pop in LD_POPULATIONS:
        l2 = rng.gamma(2.0, 10.0, len(panel)) + hot * rng.gamma(4.0, 40.0, len(panel))
        ld = pd.DataFrame({"chr": panel["chr"].to_numpy(), "pos": panel["pos38"].astype(int).to_numpy(), "l2": np.round(l2, 3)})
        with gzip.open(os.path.join(res_dir, f"ldscores_{pop}.tsv.gz"), "wt") as f:
            f.write(f"# SYNTHETIC LD scores ({pop}, GRCh38) for GWASHub local development.\n")
            ld.to_csv(f, sep="\t", index=False)


# --------------------------------------------------------------------------------------
# Traits and summary statistics
# --------------------------------------------------------------------------------------
def make_trait(rng, variants, n_loci=18, effect_sd=0.07):
    """True per-variant log-OR: causal variants + decaying LD-ish neighbours."""
    beta = np.zeros(len(variants))
    hot = np.where(variants["hotspot"].to_numpy() & variants["pos38"].notna().to_numpy())[0]
    pool = hot if len(hot) >= n_loci * 5 else np.arange(len(variants))
    causal = []
    for j in rng.permutation(pool):  # one causal variant per hotspot
        if all(variants["chr"].iat[j] != variants["chr"].iat[k] or abs(variants["pos37"].iat[j] - variants["pos37"].iat[k]) > 1_000_000 for k in causal):
            causal.append(j)
        if len(causal) == n_loci:
            break
    causal = np.array(causal)
    chr_arr = variants["chr"].to_numpy()
    pos = variants["pos37"].to_numpy()
    for c in causal:
        b = rng.normal(0, effect_sd)
        b = np.sign(b) * max(abs(b), 0.03)
        near = np.where((chr_arr == chr_arr[c]) & (np.abs(pos - pos[c]) < 400_000))[0]
        for j in near:
            r = np.exp(-abs(pos[j] - pos[c]) / 40_000) * (1 if j == c else rng.uniform(0.6, 1.0))
            beta[j] += b * r * (1 if rng.random() < 0.85 or j == c else -1)
    return beta, variants.index[causal].tolist()


@dataclass
class Cohort:
    name: str
    cohort: str
    phenotype: str
    ancestry: str
    sex: str
    n_cases: int
    n_controls: int
    build: str = "GRCh38"
    style: str = "plink"
    effect_type: str = "beta"
    compress: bool = True
    delimiter: str = "\t"
    coverage: float = 0.93
    inflation: float = 1.0
    faults: list = field(default_factory=list)
    seed: int = 0


STYLES = {
    # standard -> column name, per software style
    "plink": dict(rsid="SNP", chr="CHR", pos="BP", ea="A1", oa="A2", eaf="FRQ", beta="BETA", se="SE", p="P", n="N", info="INFO"),
    "ssf": dict(rsid="variant_id", chr="chromosome", pos="base_pair_location", ea="effect_allele", oa="other_allele",
                eaf="effect_allele_frequency", beta="beta", se="standard_error", p="p_value", n="n"),
    "metal": dict(rsid="MarkerName", chr="Chrom", pos="Pos", ea="Allele1", oa="Allele2", eaf="Freq1", beta="Effect",
                  se="StdErr", p="P-value", n="TotalN"),
    "regenie": dict(rsid="ID", chr="CHROM", pos="GENPOS", ea="ALLELE1", oa="ALLELE0", eaf="A1FREQ", beta="BETA", se="SE",
                    p="P", n="N", info="INFO"),
    "custom": dict(rsid="snpid", chr="chrom", pos="position", ea="EffectAllele", oa="NonEffectAllele", eaf="EAF",
                   beta="logOR", se="se_logOR", p="pval", n="Neff"),
}


def simulate(cohort: Cohort, variants, true_beta):
    rng = np.random.default_rng(cohort.seed)
    keep = rng.random(len(variants)) < cohort.coverage
    v = variants[keep].copy()
    b_true = true_beta[keep]
    f = v[f"af_{cohort.ancestry}"].to_numpy()
    n_eff = 4 / (1 / cohort.n_cases + 1 / cohort.n_controls)
    se = 1 / np.sqrt(n_eff * 2 * f * (1 - f) * 0.25 * 4)  # rough log-OR SE
    beta = rng.normal(b_true, se)
    if cohort.inflation != 1.0:  # inflate test statistics around the truth
        beta = b_true + (beta - b_true) * np.sqrt(cohort.inflation)
    z = beta / se
    p = 2 * stats.norm.sf(np.abs(z))
    eaf_obs = np.clip(rng.binomial(2 * (cohort.n_cases + cohort.n_controls), f) / (2 * (cohort.n_cases + cohort.n_controls)), 0.0005, 0.9995)

    # Effect allele = alt (GRCh38 forward). Randomly report half the variants with ea=ref.
    swap = rng.random(len(v)) < 0.5
    ea = np.where(swap, v["ref"], v["alt"])
    oa = np.where(swap, v["alt"], v["ref"])
    beta = np.where(swap, -beta, beta)
    eaf_obs = np.where(swap, 1 - eaf_obs, eaf_obs)

    if cohort.build == "GRCh37":
        pos = v["pos37"].to_numpy()
        # Alleles are reported on the GRCh37 forward strand: reverse-complemented in inverted segments.
        minus = (v["strand37to38"] == "-").to_numpy()
        ea = np.where(minus, [a.translate(COMP)[::-1] for a in ea], ea)  # reverse complement
        oa = np.where(minus, [a.translate(COMP)[::-1] for a in oa], oa)
    else:
        mapped = v["pos38"].notna().to_numpy()
        v, beta, se, p, ea, oa, eaf_obs = v[mapped], beta[mapped], se[mapped], p[mapped], ea[mapped], oa[mapped], eaf_obs[mapped]
        pos = v["pos38"].astype(int).to_numpy()

    n_total = cohort.n_cases + cohort.n_controls
    out = pd.DataFrame({
        "rsid": v["rsid"].to_numpy(), "chr": v["chr"].to_numpy(), "pos": pos, "ea": ea, "oa": oa,
        "eaf": np.round(eaf_obs, 4), "beta": np.round(beta, 5), "se": np.round(se, 5),
        "p": [f"{x:.4g}" for x in p], "n": n_total,
        "info": np.round(np.clip(rng.beta(12, 1, len(v)), 0.3, 1), 3),
    })
    return apply_faults(out, cohort, rng)


def apply_faults(df, cohort, rng):
    faults = set(cohort.faults)
    if cohort.effect_type == "or":
        df["beta"] = np.round(np.exp(df["beta"].astype(float)), 5)
    if "neglog10p" in faults:
        df["p"] = [f"{-np.log10(float(x)):.4g}" for x in df["p"]]
    if "swapped_allele_columns" in faults:
        df["ea"], df["oa"] = df["oa"].copy(), df["ea"].copy()
    if "duplicates" in faults:
        df = pd.concat([df, df.sample(frac=0.01, random_state=1)])
    if "missing_values" in faults:
        idx = rng.choice(len(df), int(0.012 * len(df)), replace=False)
        df.iloc[idx, df.columns.get_loc("se")] = np.nan
    if "chr_prefix" in faults:
        df["chr"] = "chr" + df["chr"].astype(str)
    if "lowercase_alleles" in faults:
        df["ea"], df["oa"] = df["ea"].str.lower(), df["oa"].str.lower()
    if "drop_se" in faults:
        df = df.drop(columns=["se"])
    return df.sort_values(["chr", "pos"])


def write_dataset(df, cohort: Cohort, out_dir):
    cols = STYLES[cohort.style]
    df = df[[c for c in cols if c in df.columns]].rename(columns=cols)
    ext = {"\t": "tsv", ",": "csv", " ": "txt"}[cohort.delimiter]
    fname = f"{cohort.name}.{ext}" + (".gz" if cohort.compress else "")
    path = os.path.join(out_dir, fname)
    df.to_csv(path, sep=cohort.delimiter, index=False, compression="gzip" if cohort.compress else None)
    mapping = {std: col for std, col in cols.items() if col in df.columns}
    return fname, mapping


# --------------------------------------------------------------------------------------
# Demo set
# --------------------------------------------------------------------------------------
def demo_cohorts():
    C = Cohort
    return [
        # HERMES-like heart failure cohorts: mostly clean, varied formats/builds.
        C("ukb_hf_eur", "UK Biobank", "All-cause HF", "EUR", "Combined", 12000, 380000, "GRCh37", "plink", seed=1),
        C("finngen_hf_eur", "FinnGen", "All-cause HF", "EUR", "Combined", 9000, 250000, "GRCh38", "regenie", seed=2),
        C("mvp_hf_afr", "MVP", "All-cause HF", "AFR", "Combined", 5200, 60000, "GRCh38", "ssf", seed=3),
        C("bbj_hf_eas", "BBJ", "All-cause HF", "EAS", "Combined", 4100, 150000, "GRCh37", "metal", effect_type="or",
          delimiter=",", compress=False, seed=4),
        C("estbb_hf_eur_f", "EstBB", "All-cause HF", "EUR", "Female", 2600, 70000, "GRCh38", "plink", seed=5,
          faults=["missing_values", "duplicates", "chr_prefix"]),
        C("hunt_hf_eur", "HUNT", "All-cause HF", "EUR", "Combined", 3900, 60000, "GRCh37", "custom", inflation=1.18,
          faults=["lowercase_alleles"], seed=6),
        C("estbb_hf_eur_m", "EstBB", "All-cause HF", "EUR", "Male", 3100, 62000, "GRCh38", "plink", seed=14),
        # Broken submissions the QC should catch.
        C("biovu_hf_eur_neglog10p", "BioVU", "All-cause HF", "EUR", "Combined", 3000, 50000, "GRCh38", "plink",
          faults=["neglog10p"], seed=7),
        C("partners_hf_eur_swapped", "Partners Biobank", "All-cause HF", "EUR", "Combined", 2800, 40000, "GRCh38", "ssf",
          faults=["swapped_allele_columns"], seed=8),
        C("decode_hf_eur_wrongbuild", "deCODE", "All-cause HF", "EUR", "Combined", 5000, 200000, "GRCh37", "plink",
          seed=9),  # declared as GRCh38 in the manifest below
        C("gnh_hf_sas_nose", "Genes & Health", "All-cause HF", "SAS", "Combined", 1500, 40000, "GRCh38", "plink",
          faults=["drop_se"], seed=10),
        # HCM consortium: a second trait with its own causal loci.
        C("share_hcm_eur", "SHaRe", "HCM", "EUR", "Combined", 6000, 60000, "GRCh38", "ssf", seed=11),
        C("hcmr_hcm_eur", "HCMR", "HCM", "EUR", "Combined", 2700, 40000, "GRCh37", "plink", seed=12),
        C("pk_hcm_sas", "Pakistan HCM", "HCM", "SAS", "Combined", 1800, 20000, "GRCh38", "custom", seed=13),
    ]


def generate(out, n_variants=40000, small=False, seed=20261002):
    rng = np.random.default_rng(seed)
    res_dir, ds_dir = os.path.join(out, "resources"), os.path.join(out, "fake_gwas")
    os.makedirs(res_dir, exist_ok=True)
    os.makedirs(ds_dir, exist_ok=True)
    if small:
        n_variants = 3000
        hotspots = 8
    else:
        hotspots = 30
    segments, q_sizes = make_chain(rng)
    write_chain(os.path.join(res_dir, "hg19ToHg38.over.chain.gz"), segments, q_sizes)
    variants = make_variants(rng, n_variants, segments, q_sizes, n_hotspots=hotspots)
    write_ref_panel(os.path.join(res_dir, "ref_panel_grch38.tsv.gz"), variants)
    write_ld_scores(rng, res_dir, variants)
    variants.to_csv(os.path.join(out, "truth_variants.tsv.gz"), sep="\t", index=False)
    traits, truth = {}, []
    for pheno, n_loci in (("All-cause HF", 6 if small else 20), ("HCM", 3 if small else 8)):
        tb, causal = make_trait(rng, variants, n_loci=n_loci)
        traits[pheno] = tb
        truth.append(pd.DataFrame({"phenotype": pheno, "variant": variants.loc[causal, "rsid"], "chr": variants.loc[causal, "chr"],
                                   "pos38": variants.loc[causal, "pos38"], "beta": tb[causal]}))
    pd.concat(truth).to_csv(os.path.join(out, "truth_causal.tsv"), sep="\t", index=False)

    manifest = []
    cohorts = demo_cohorts()
    if small:
        cohorts = [c for c in cohorts if c.name in ("ukb_hf_eur", "finngen_hf_eur", "bbj_hf_eas")]
        for c in cohorts:
            c.n_cases, c.n_controls = c.n_cases * 20, c.n_controls * 5  # strong signal in tiny data
    for c in cohorts:
        df = simulate(c, variants, traits[c.phenotype])
        fname, mapping = write_dataset(df, c, ds_dir)
        declared_build = "GRCh38" if c.name.endswith("wrongbuild") else c.build
        manifest.append({
            "file": fname, "cohort": c.cohort, "phenotype": c.phenotype, "ancestry": c.ancestry, "sex": c.sex,
            "n_cases": c.n_cases, "n_controls": c.n_controls, "build": declared_build,
            "effect_type": c.effect_type, "column_mapping": mapping, "faults": c.faults,
            "true_build": c.build, "inflation": c.inflation, "rows": len(df),
        })
    with open(os.path.join(out, "fake_gwas", "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="local/synthetic")
    ap.add_argument("--variants", type=int, default=40000)
    ap.add_argument("--small", action="store_true")
    a = ap.parse_args()
    m = generate(a.out, a.variants, a.small)
    for d in m:
        print(f"{d['file']:<40} {d['rows']:>7} rows  {d['ancestry']}  {d['build']}  faults={d['faults']}")


if __name__ == "__main__":
    main()


PARAM_KEYS = ("column_mapping", "build", "ancestry", "effect_type", "n_cases", "n_controls", "cohort", "phenotype", "sex")


def params_for(entry):
    """Run params for a manifest entry (what the upload form would submit)."""
    return {k: entry[k] for k in PARAM_KEYS}
