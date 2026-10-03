"""Allele checks: alleles must be A/C/G/T sequences; count palindromic SNPs and indels."""
from _lib.common import complement, load_params, parse_args, read_working, step_params, write_log, write_working

DEFAULTS = {"drop_indels": False, "warn_invalid_fraction": 0.01}


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    df = read_working(args.input)
    n0 = len(df)

    valid = df["ea"].str.fullmatch("[ACGT]+", na=False) & df["oa"].str.fullmatch("[ACGT]+", na=False) & (df["ea"] != df["oa"])
    invalid = int((~valid).sum())
    df = df[valid]
    snp = (df["ea"].str.len() == 1) & (df["oa"].str.len() == 1)
    palindromic = snp & (df["ea"].map(complement) == df["oa"])
    indels = int((~snp).sum())
    messages = [f"{int(palindromic.sum()):,} palindromic SNPs flagged (resolved later by reference_check)."]
    if opts["drop_indels"] and indels:
        df = df[snp]
        messages.append(f"Dropped {indels:,} indels.")
    if invalid:
        messages.append(f"Dropped {invalid:,} rows with invalid alleles.")

    frac = invalid / max(n0, 1)
    write_working(df, args.input)
    write_log(args.log, "alleles", "warn" if frac > opts["warn_invalid_fraction"] else "pass",
              {"invalid": invalid, "palindromic": int(palindromic.sum()), "indels": indels, "rows_out": len(df)}, messages)


if __name__ == "__main__":
    main()
