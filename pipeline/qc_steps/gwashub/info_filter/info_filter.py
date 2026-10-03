"""Imputation INFO filter: drop poorly imputed variants."""
from _lib.common import load_params, parse_args, read_working, step_params, write_log, write_working

DEFAULTS = {"info_threshold": 0.8}


def main():
    args = parse_args()
    opts = step_params(load_params(args.params), DEFAULTS)
    df = read_working(args.input)
    if "info" not in df or df["info"].isna().all():
        write_log(args.log, "info_filter", "warn", {}, ["No INFO column; INFO filter skipped."])
        return
    keep = df["info"].isna() | (df["info"] >= opts["info_threshold"])
    dropped = int((~keep).sum())
    write_working(df[keep], args.input)
    write_log(args.log, "info_filter", "pass", {"dropped_low_info": dropped, "threshold": opts["info_threshold"],
                                                "rows_out": int(keep.sum())},
              [f"Dropped {dropped:,} variants with INFO < {opts['info_threshold']}."])


if __name__ == "__main__":
    main()
