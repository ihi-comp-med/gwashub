"""MAF filter: drop variants below a minor-allele-frequency threshold."""
from _lib.common import load_params, parse_args, read_working, step_params, write_log, write_working

DEFAULTS = {"maf_threshold": 0.01}


def main():
    args = parse_args()
    opts = step_params(load_params(args.params), DEFAULTS)
    df = read_working(args.input)
    if "eaf" not in df or df["eaf"].isna().all():
        write_log(args.log, "maf_filter", "warn", {}, ["No EAF column; MAF filter skipped."])
        return
    maf = df["eaf"].where(df["eaf"] <= 0.5, 1 - df["eaf"])
    keep = maf >= opts["maf_threshold"]
    dropped = int((~keep).sum())
    write_working(df[keep], args.input)
    write_log(args.log, "maf_filter", "pass", {"dropped_low_maf": dropped, "threshold": opts["maf_threshold"],
                                               "rows_out": int(keep.sum())},
              [f"Dropped {dropped:,} variants with MAF < {opts['maf_threshold']}."])


if __name__ == "__main__":
    main()
