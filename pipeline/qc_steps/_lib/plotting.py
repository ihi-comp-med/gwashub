"""Manhattan + QQ plots shared by the plots step and the meta-analysis."""
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

COLORS = ("#303880", "#8b95cc")  # HERMES blue + light


def lambda_gc(p):
    p = np.asarray(p, dtype=float)
    p = p[np.isfinite(p) & (p > 0)]
    if not len(p):
        return float("nan")
    return float(np.median(stats.chi2.isf(np.clip(p, 1e-300, 1), 1)) / stats.chi2.ppf(0.5, 1))


def make_plots(df, out_dir, gws=5e-8, max_points=6000, title=None):
    """df needs chr, pos, p. Writes manhattan.png, qq.png, plot_data.json. Returns (lambda, n)."""
    df = df[["chr", "pos", "p"]].dropna().copy()
    df["chr"] = df["chr"].astype(str)
    df = df[df["chr"].isin([str(i) for i in range(1, 23)] + ["X"])]
    df["cn"] = df["chr"].map(lambda c: 23 if c == "X" else int(c))
    df = df.sort_values(["cn", "pos"])
    df["logp"] = -np.log10(df["p"].clip(lower=1e-300))

    offsets, ticks, off = {}, [], 0
    for c, g in df.groupby("cn", sort=True):
        offsets[c] = off
        ticks.append((c, off + g["pos"].max() / 2))
        off += g["pos"].max() + 5_000_000
    df["x"] = df["pos"] + df["cn"].map(offsets)

    fig, ax = plt.subplots(figsize=(11, 3.6), dpi=110)
    for c, g in df.groupby("cn"):
        ax.scatter(g["x"], g["logp"], s=3, c=COLORS[c % 2], linewidths=0, rasterized=True)
    ax.axhline(-np.log10(gws), color="#c2410c", ls="--", lw=0.8)
    ax.set_xticks([t[1] for t in ticks], [("X" if t[0] == 23 else str(t[0])) for t in ticks], fontsize=7)
    ax.set_xlim(0, max(off, 1))
    ax.set_ylim(0, max(10, df["logp"].max() * 1.05 if len(df) else 10))
    ax.set_xlabel("Chromosome")
    ax.set_ylabel("−log10(P)")
    if title:
        ax.set_title(title, fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "manhattan.png"))
    plt.close(fig)

    obs = np.sort(df["logp"].to_numpy())[::-1]
    n = len(obs)
    exp = -np.log10((np.arange(1, n + 1) - 0.5) / max(n, 1))
    lam = lambda_gc(df["p"])
    fig, ax = plt.subplots(figsize=(4, 4), dpi=110)
    ax.scatter(exp, obs, s=3, c=COLORS[0], linewidths=0, rasterized=True)
    lim = max(exp.max() if n else 1, 1)
    ax.plot([0, lim], [0, lim], color="#c2410c", lw=0.8, ls="--")
    ax.set_xlabel("Expected −log10(P)")
    ax.set_ylabel("Observed −log10(P)")
    ax.text(0.05, 0.92, f"λGC = {lam:.3f}", transform=ax.transAxes)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "qq.png"))
    plt.close(fig)

    strong = df[df["p"] < 1e-3]
    rest = df[df["p"] >= 1e-3]
    k = max(0, max_points - len(strong))
    sample = pd.concat([strong, rest.sample(min(k, len(rest)), random_state=1)]).sort_values("x")
    qq_idx = np.unique(np.concatenate([np.arange(min(500, n)), np.linspace(0, max(n - 1, 0), min(500, n)).astype(int)])) if n else np.array([], int)
    with open(os.path.join(out_dir, "plot_data.json"), "w") as f:
        json.dump({
            "manhattan": {"x": sample["x"].round(0).astype(int).tolist(), "y": sample["logp"].round(3).tolist(),
                          "chr": sample["cn"].tolist(), "pos": sample["pos"].astype(int).tolist(),
                          "ticks": [{"chr": "X" if c == 23 else str(c), "x": int(x)} for c, x in ticks], "x_max": int(off)},
            "qq": {"expected": exp[qq_idx].round(3).tolist(), "observed": obs[qq_idx].round(3).tolist(),
                   "lambda": None if np.isnan(lam) else round(lam, 4)},
            "gws": gws,
        }, f)
    return lam, n
