"""Value ranges: drop missing/out-of-range values and duplicate variants; catch -log10(P) files."""
import numpy as np

from _lib.common import load_params, parse_args, read_working, step_params, write_log, write_working

DEFAULTS = {"warn_drop_fraction": 0.005, "fail_drop_fraction": 0.2, "neglog10_p_fraction": 0.05}


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    df = read_working(args.input)
    n0 = len(df)

    p_above_one = int((df["p"] > 1).sum())
    if n0 and p_above_one / n0 > opts["neglog10_p_fraction"]:
        write_log(args.log, "value_ranges", "fail", {"p_above_1": p_above_one, "total_rows": n0},
                  [f"{p_above_one / n0:.0%} of P-values are above 1: the column looks like −log10(P). "
                   "Convert it (P = 10^-x) or map the right column, then re-upload."])
        return

    df = df.replace([np.inf, -np.inf], np.nan)
    na = df[["pos", "beta", "se", "p"]].isna().any(axis=1)
    if "eaf" in df:
        na |= df["eaf"].isna()
    dropped_na = int(na.sum())
    df = df[~na]

    ok = (df["p"] > 0) & (df["p"] <= 1) & (df["se"] > 0) & (df["pos"] > 0)
    if "eaf" in df:
        ok &= df["eaf"].between(0, 1)
    dropped_range = int((~ok).sum())
    df = df[ok]

    key = ["chr", "pos", "ea", "oa"]
    dup = df.duplicated(key, keep="first")
    duplicates = int(dup.sum())
    df = df[~dup]
    df["pos"] = df["pos"].astype(int)

    dropped = dropped_na + dropped_range + duplicates
    frac = dropped / max(n0, 1)
    verdict = "fail" if frac > opts["fail_drop_fraction"] else "warn" if frac > opts["warn_drop_fraction"] else "pass"
    messages = [] if dropped else ["All rows passed the range checks; no duplicates."]
    if dropped:
        messages.append(f"Dropped {dropped:,} rows ({frac:.2%}): {dropped_na:,} missing, "
                        f"{dropped_range:,} out of range, {duplicates:,} duplicate variants.")
    write_working(df, args.input)
    write_log(args.log, "value_ranges", verdict,
              {"dropped_na": dropped_na, "dropped_out_of_range": dropped_range, "duplicates": duplicates,
               "dropped_fraction": round(frac, 5), "rows_out": len(df)}, messages)


if __name__ == "__main__":
    main()
