"""Harmonise columns: rename to the GWASHub schema, OR -> BETA, normalise chromosomes and alleles."""
import numpy as np
import pandas as pd

from _lib.common import CHROMS, REQUIRED, STANDARD, load_params, parse_args, read_working, step_params, write_log, write_working

DEFAULTS = {"keep_chromosomes": CHROMS}


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    colmap = {k: v for k, v in (params.get("column_mapping") or {}).items() if v and k in STANDARD}

    missing = [c for c in REQUIRED if c not in colmap]
    if missing:
        write_log(args.log, "harmonise_columns", "fail", {"missing_required": len(missing)},
                  [f"Required column(s) not mapped: {', '.join(missing)}. Re-map columns and re-run."])
        return

    raw = pd.read_csv(args.input, sep="\t", dtype=str, keep_default_na=False, na_values=["", "NA"])
    df = pd.DataFrame({std: raw[col] for std, col in colmap.items()})
    messages = [f"Mapped {len(colmap)} columns to the standard schema."]

    for c in ("pos", "beta", "se", "p", "eaf", "n", "info"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    or_converted = 0
    if (params.get("effect_type") or "beta").lower() == "or":
        bad = df["beta"] <= 0
        df.loc[bad, "beta"] = np.nan
        df["beta"] = np.log(df["beta"])
        or_converted = int((~bad).sum())
        messages.append("Converted odds ratios to log-odds (BETA).")

    if "n" not in df:
        n = params.get("n_total") or ((params.get("n_cases") or 0) + (params.get("n_controls") or 0)) or None
        df["n"] = n
        if n:
            messages.append(f"No N column; filled N = {n:,} from the study details.")

    chrom = df["chr"].astype(str).str.strip().str.upper().str.replace(r"^CHR", "", regex=True)
    chrom = chrom.replace({"23": "X", "25": "X"})
    keep = chrom.isin([str(c) for c in opts["keep_chromosomes"]])
    dropped_chr = int((~keep).sum())
    df["chr"] = chrom
    df = df[keep]
    if dropped_chr:
        messages.append(f"Dropped {dropped_chr:,} rows on other chromosomes/contigs.")

    lower = int(df["ea"].str.contains("[a-z]", na=False).sum())
    df["ea"] = df["ea"].str.strip().str.upper()
    df["oa"] = df["oa"].str.strip().str.upper()
    if lower:
        messages.append(f"Upper-cased {lower:,} lower-case alleles.")

    df = df[[c for c in STANDARD if c in df]]
    write_working(df, args.input)
    write_log(args.log, "harmonise_columns", "pass",
              {"renamed_columns": len(colmap), "or_to_beta": or_converted, "dropped_other_chromosomes": dropped_chr,
               "rows_out": len(df)}, messages)


if __name__ == "__main__":
    main()
