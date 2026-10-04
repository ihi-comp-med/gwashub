"""Column profile of a summary-statistics file: rows, and per numeric column the count, missing values,
mean, median, SD, min and max.

The runner profiles every QC run twice, whatever the pipeline: the upload as received (columns found
through the dataset's column mapping, values as uploaded, e.g. odds ratios before conversion) and the
cleaned file at the end. qc_report shows both side by side.
"""
import math

import numpy as np
import pandas as pd

COLUMNS = ["beta", "se", "p", "eaf", "n", "info"]


def _num(x):
    return None if x is None or not math.isfinite(x) else float(f"{x:.6g}")


def profile_frame(df: pd.DataFrame) -> dict:
    """{"rows": n, "columns": {col: {n, missing, mean, median, sd, min, max}}} for the standard numeric columns present."""
    cols = {}
    for c in COLUMNS:
        if c not in df:
            continue
        v = pd.to_numeric(df[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
        ok = v.dropna()
        cols[c] = {"n": int(len(ok)), "missing": int(len(v) - len(ok)),
                   **({k: None for k in ("mean", "median", "sd", "min", "max")} if ok.empty else
                      {"mean": _num(ok.mean()), "median": _num(ok.median()), "sd": _num(ok.std()) if len(ok) > 1 else None,
                       "min": _num(ok.min()), "max": _num(ok.max())})}
    return {"rows": int(len(df)), "columns": cols}


def profile_upload(path: str, column_mapping: dict | None) -> dict:
    """Profile of the file as uploaded (any delimiter, optionally gzipped), standard columns via the mapping."""
    from _lib.common import read_raw
    raw, _ = read_raw(path)
    mapping = {std: col for std, col in (column_mapping or {}).items() if std in COLUMNS and col in raw.columns}
    return profile_frame(pd.DataFrame({std: raw[col] for std, col in mapping.items()}, index=raw.index))


def profile_working(path: str) -> dict:
    """Profile of a harmonised working file (tab-separated, standard column names)."""
    return profile_frame(pd.read_csv(path, sep="\t", usecols=lambda c: c in COLUMNS))
