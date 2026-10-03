"""Row check: the uploaded file parses, mapped columns exist and look numeric.

Reads the raw upload (tab/comma/whitespace, optionally gzipped) and rewrites it as a plain
tab-separated working file with the original headers, so later steps have one format.
(Range checks moved to value_ranges; this was originally qc-pipeline's row_check.py.)
"""
import pandas as pd

from _lib.common import REQUIRED, load_params, parse_args, read_raw, step_params, write_log

NUMERIC = ["pos", "beta", "se", "p", "eaf", "n", "info"]
DEFAULTS = {"min_numeric_fraction": 0.9, "min_rows": 1000}


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    colmap = params.get("column_mapping") or {}

    try:
        df, fmt = read_raw(args.input)
    except Exception as e:  # unparseable file is a QC failure, not a crash
        write_log(args.log, "row_check", "fail", messages=[f"Could not parse the file: {e}"])
        return

    metrics = {"total_rows": len(df), "columns": len(df.columns), **fmt}
    problems = []
    missing_map = [c for c in REQUIRED if not colmap.get(c)]
    if missing_map:
        problems.append(f"Required column(s) not mapped: {', '.join(missing_map)}.")
    absent = [f"{std} → {col}" for std, col in colmap.items() if col and col not in df.columns]
    if absent:
        problems.append(f"Mapped column(s) not in the file header: {', '.join(absent)}.")
    if len(df) < opts["min_rows"]:
        problems.append(f"Only {len(df)} rows; expected at least {opts['min_rows']}.")

    for std in NUMERIC:
        col = colmap.get(std)
        if col and col in df.columns:
            frac = pd.to_numeric(df[col], errors="coerce").notna().mean() if len(df) else 0
            metrics[f"numeric_fraction_{std}"] = round(float(frac), 4)
            if frac < opts["min_numeric_fraction"]:
                problems.append(f"Column {col} ({std}) is only {frac:.0%} numeric.")

    df.to_csv(args.input, sep="\t", index=False, na_rep="NA")
    if problems:
        write_log(args.log, "row_check", "fail", metrics, problems)
    else:
        write_log(args.log, "row_check", "pass", metrics,
                  [f"Parsed {len(df):,} rows ({fmt['delimiter']}-delimited{', gzipped' if fmt['compressed'] else ''})."])


if __name__ == "__main__":
    main()
