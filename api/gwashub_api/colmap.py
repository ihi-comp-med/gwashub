"""Suggest a column mapping from a file header (case/punctuation-insensitive synonyms)."""
import re

SYNONYMS = {
    "rsid": ["rsid", "snp", "snpid", "markername", "variantid", "id", "rs", "rsnumber", "marker", "variant"],
    "chr": ["chr", "chrom", "chromosome", "chrnum", "hg19chr", "hg38chr", "ch"],
    "pos": ["pos", "bp", "position", "basepairlocation", "genpos", "bpos", "posb37", "posb38", "location"],
    "ea": ["ea", "effectallele", "a1", "allele1", "alt", "testedallele", "riskallele", "codedallele", "effectallelea1"],
    "oa": ["oa", "otherallele", "noneffectallele", "nea", "a2", "allele2", "allele0", "ref", "referenceallele", "noncodedallele"],
    "eaf": ["eaf", "effectallelefrequency", "freq", "freq1", "frq", "a1freq", "af", "maf", "effectallelefreq", "freqa1"],
    "beta": ["beta", "effect", "b", "logor", "or", "oddsratio", "estimate", "betas"],
    "se": ["se", "standarderror", "stderr", "selogor", "sebeta", "sterr"],
    "p": ["p", "pval", "pvalue", "pvalues", "pv", "pval1", "pwald", "plrt", "pvaluegc"],
    "n": ["n", "ntotal", "totaln", "neff", "samplesize", "nsamples", "nobs"],
    "info": ["info", "rsq", "r2", "imputationinfo", "infoscore", "impinfo"],
}


def _norm(c: str) -> str:
    return re.sub(r"[^a-z0-9]", "", c.lower())


def suggest(columns: list[str]) -> dict:
    """Returns {"mapping": {std: column}, "effect_type": "or"|"beta"}."""
    normed = {_norm(c): c for c in columns}
    mapping, used = {}, set()
    for std, names in SYNONYMS.items():
        for n in names:
            if n in normed and normed[n] not in used:
                mapping[std] = normed[n]
                used.add(normed[n])
                break
    effect = "or" if mapping.get("beta") and _norm(mapping["beta"]) in ("or", "oddsratio") else "beta"
    return {"mapping": mapping, "effect_type": effect}
