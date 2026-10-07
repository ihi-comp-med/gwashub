"""Shared helpers for GWASHub default steps.

Default steps run with PYTHONPATH including the steps/ folder, so they can
`from _lib.common import ...`. Custom steps in other repos can't rely on this;
they only need to honour the contract (see steps/README.md).
"""
import argparse
import csv
import gzip
import json
import math
import os

import numpy as np
import pandas as pd

# GWASHub format: the working file every step after gwashub/parse reads and writes (tab-separated, header,
# "NA" for missing). REQUIRED columns are always there; the others when the upload or the study details gave them.
# Positions are in the uploaded build until gwashub/harmonise_liftover, GRCh38 (ea = ALT) after it.
STANDARD = ["chr", "pos", "ea", "oa", "beta", "se", "p", "eaf", "n", "ncase", "ncontrol", "rsid", "info"]
REQUIRED = ["chr", "pos", "ea", "oa", "beta", "se", "p", "eaf"]  # parse removes rows without any of these
CHROMS_XY = [str(i) for i in range(1, 23)] + ["X", "Y"]  # chromosomes the format allows


def parse_args(extra=()):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--resources", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--log", required=True)
    for flag in extra:
        ap.add_argument(flag)
    return ap.parse_args()


def load_params(path):
    with open(path) as f:
        return json.load(f)


def step_params(params, defaults):
    """Per-step params (params['step']) over the step's defaults."""
    merged = dict(defaults)
    merged.update(params.get("step") or {})
    return merged


def artifact_dir():
    d = os.environ.get("GWASHUB_ARTIFACT_DIR")
    if d:
        os.makedirs(d, exist_ok=True)
    return d


def is_gzip(path):
    with open(path, "rb") as f:
        return f.read(2) == b"\x1f\x8b"


def sniff_delimiter(sample):
    first = sample.splitlines()[0] if sample else ""
    if "\t" in first:
        return "\t"
    if first.count(",") >= 2:
        return ","
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t, ;").delimiter
    except csv.Error:
        return r"\s+"


def read_raw(path):
    """Read an uploaded file (any delimiter, optionally gzipped) as strings."""
    gz = is_gzip(path)
    opener = gzip.open if gz else open
    with opener(path, "rt", newline="") as f:
        sample = f.read(64 * 1024)
    sep = sniff_delimiter(sample)
    df = pd.read_csv(path, sep=sep, dtype=str, compression="gzip" if gz else None,
                     engine="python" if sep == r"\s+" else "c", keep_default_na=False, na_values=["", "NA", "NaN", "nan", "."])
    return df, {"delimiter": {"\t": "tab", ",": "comma"}.get(sep, "whitespace"), "compressed": gz}


def read_working(path):
    return pd.read_csv(path, sep="\t", dtype={"chr": str, "ea": str, "oa": str, "rsid": str}, low_memory=False)


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def write_log(path, step, verdict, metrics=None, messages=None, artifacts=None):
    assert verdict in ("pass", "warn", "fail"), verdict
    with open(path, "w") as f:
        json.dump(_clean({
            "step": step,
            "verdict": verdict,
            "metrics": metrics or {},
            "messages": messages or [],
            "artifacts": artifacts or [],
        }), f, indent=2)
