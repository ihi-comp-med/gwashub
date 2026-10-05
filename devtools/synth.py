"""Synthetic genome, reference resources and fake GWAS summary statistics for local development.

Everything here is SYNTHETIC: a made-up GRCh37 -> GRCh38 chain (with gaps and an inverted
segment), reference genomes for both builds (all N except the variants' reference alleles),
a dbSNP-like VCF, 1000 Genomes-like allele frequencies, LD scores in LDSC's format, and
simulated summary statistics with shared causal loci. Resources use the production file
names so the default pipeline runs offline and deterministically. Do not use these files for
real analyses.

    python -m devtools.synth --out local/synthetic   # resources + the demo datasets
    python -m devtools.synth --out /tmp/x --small  # tiny version (tests)
"""
import argparse
import gzip
import json
import os
import struct
import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

ANCESTRIES = ["EUR", "AFR", "EAS", "SAS", "AMR"]
# GRCh37 autosome lengths (bp).
CHR_LEN_37 = [249250621, 243199373, 198022430, 191154276, 180915260, 171115067, 159138663,
              146364022, 141213431, 135534747, 135006516, 133851895, 115169878, 107349540,
              102531392, 90354753, 81195210, 78077248, 59128983, 63025520, 48129895, 51304566]
COMP = str.maketrans("ACGT", "TGCA")


# --------------------------------------------------------------------------------------
# Chain: GRCh37 -> GRCh38
# --------------------------------------------------------------------------------------
@dataclass
class Segment:
    chr: str
    t_start: int  # 0-based, GRCh37
    size: int
    q_start: int  # 0-based forward-strand GRCh38 start of this segment
    strand: str  # '+' or '-'


def make_chain(rng, inverted_chroms=("5", "16")):
    """Segments mapping GRCh37 to GRCh38, plus GRCh38 chromosome sizes.

    Each chromosome is cut into 4-9 segments separated by short unmapped GRCh37 gaps;
    GRCh38 gets small insertions between segments, so positions shift. One segment on each
    of `inverted_chroms` is inverted (maps to the minus strand).
    """
    segments, q_sizes = [], {}
    for i, length in enumerate(CHR_LEN_37):
        c = str(i + 1)
        n = int(rng.integers(4, 10))
        cuts = np.sort(rng.choice(np.arange(1, length - 1, 1_000_000), n - 1, replace=False))
        bounds = [0, *cuts.tolist(), length]
        q = int(rng.integers(0, 200_000))
        inv = int(rng.integers(1, n - 1)) if c in inverted_chroms else -1
        for k in range(n):
            gap = int(rng.integers(20_000, 300_000)) if k > 0 else 0
            t0 = bounds[k] + gap
            size = bounds[k + 1] - t0
            if size <= 0:
                continue
            segments.append(Segment(c, t0, size, q, "-" if k == inv else "+"))
            q += size + int(rng.integers(0, 150_000))
        q_sizes[c] = q + int(rng.integers(0, 100_000))
    return segments, q_sizes


def write_chain(path, segments, q_sizes):
    """SYNTHETIC chain (not real GRCh37 -> GRCh38). No comment line: bcftools +liftover, like UCSC's own tools, reads
    only chain records."""
    with gzip.open(path, "wt") as f:
        for i, s in enumerate(segments):
            t_size = CHR_LEN_37[int(s.chr) - 1]
            q_size = q_sizes[s.chr]
            if s.strand == "+":
                qs = s.q_start
            else:  # minus-strand coordinates
                qs = q_size - (s.q_start + s.size)
            f.write(f"chain 1000 chr{s.chr} {t_size} + {s.t_start} {s.t_start + s.size} "
                    f"chr{s.chr} {q_size} {s.strand} {qs} {qs + s.size} {i + 1}\n{s.size}\n\n")


def lift(chrom, pos37, segments, q_sizes):
    """Reference implementation used to build the truth table (1-based in/out)."""
    t = pos37 - 1
    for s in segments:
        if s.chr == chrom and s.t_start <= t < s.t_start + s.size:
            off = t - s.t_start
            q = s.q_start + off if s.strand == "+" else s.q_start + s.size - 1 - off
            return q + 1, s.strand
    return None, None


# --------------------------------------------------------------------------------------
# Variants and reference panel
# --------------------------------------------------------------------------------------
def random_alleles(rng, n):
    """Mostly non-palindromic SNPs, ~10% palindromic, ~5% indels."""
    bases = np.array(list("ACGT"))
    ref, alt = [], []
    for _ in range(n):
        u = rng.random()
        if u < 0.05:  # indel
            r = str(rng.choice(bases))
            a = r + "".join(rng.choice(bases, int(rng.integers(1, 4))))
            if rng.random() < 0.5:
                r, a = a, r
        elif u < 0.15:  # palindromic
            r = str(rng.choice(bases))
            a = r.translate(COMP)
        else:
            while True:
                r, a = rng.choice(bases, 2, replace=False)
                if r.translate(COMP) != a:
                    break
            r, a = str(r), str(a)
        ref.append(r)
        alt.append(a)
    return ref, alt


def make_variants(rng, n_variants, segments, q_sizes, n_hotspots=30, per_hotspot=120):
    """Sparse genome-wide variants plus dense clusters ("hotspots") where causal loci go, so
    association peaks have realistic LD towers rather than single points."""
    total = sum(CHR_LEN_37)
    per_chr = [max(50, int(n_variants * L / total)) for L in CHR_LEN_37]
    hot_chr = rng.choice(len(CHR_LEN_37), n_hotspots, p=np.array(CHR_LEN_37) / total)
    rows = []
    for i, n in enumerate(per_chr):
        c = str(i + 1)
        pos = rng.choice(np.arange(10_000, CHR_LEN_37[i] - 10_000), n, replace=False)
        for _ in range(int((hot_chr == i).sum())):
            centre = int(rng.integers(1_000_000, CHR_LEN_37[i] - 1_000_000))
            pos = np.concatenate([pos, centre + rng.integers(-250_000, 250_000, per_hotspot)])
        pos = np.unique(pos)
        for p in pos:
            p38, strand = lift(c, int(p), segments, q_sizes)
            rows.append((c, int(p), p38, strand))
    df = pd.DataFrame(rows, columns=["chr", "pos37", "pos38", "strand37to38"])
    ref, alt = random_alleles(rng, len(df))
    df["ref"], df["alt"] = ref, alt  # on the GRCh38 forward strand
    df["rsid"] = [f"rs{1000000 + i}" for i in range(len(df))]
    # Allele frequencies: ancestral freq + Balding-Nichols drift per ancestry.
    p0 = np.clip(rng.beta(0.7, 0.7, len(df)), 0.02, 0.98)
    df["hotspot"] = False
    for anc, fst in zip(ANCESTRIES, [0.05, 0.12, 0.10, 0.08, 0.06]):
        a = p0 * (1 - fst) / fst
        b = (1 - p0) * (1 - fst) / fst
        df[f"af_{anc}"] = np.clip(rng.beta(a, b), 0.005, 0.995).round(4)
    for c, g in df.groupby("chr"):
        p = np.sort(g["pos37"].to_numpy())
        lo = np.searchsorted(p, g["pos37"].to_numpy() - 50_000)
        hi = np.searchsorted(p, g["pos37"].to_numpy() + 50_000)
        df.loc[g.index, "hotspot"] = (hi - lo) >= 8
    return df


LD_POPULATIONS = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID")
LD_FREQ = {"AFR": "AFR", "AMR": "AMR", "CSA": "SAS", "EAS": "EAS", "EUR": "EUR", "MID": "EUR"}  # whose frequencies set M_5_50


def write_ldsc_reference(rng, res_dir, variants):
    """Synthetic LD scores in LDSC's format, named as the Pan-UK Biobank release the gwashub/ldsc step expects:
    UKBB.<POP>.l2.ldscore.gz (CHR SNP BP L2) and UKBB.<POP>.l2.M_5_50, plus the HapMap 3 list w_hm3.snplist.
    Higher in the hotspots where simulated signal sits, so polygenic signal shows up as slope (as with real LD)
    rather than intercept."""
    panel = variants[variants["pos38"].notna()].sort_values(["chr", "pos38"], key=lambda c: c.astype(int) if c.name == "chr" else c)
    hot = panel["hotspot"].to_numpy().astype(float)
    snp = (panel["ref"].str.len() == 1) & (panel["alt"].str.len() == 1)
    panel[snp][["rsid", "alt", "ref"]].rename(columns={"rsid": "SNP", "alt": "A1", "ref": "A2"}).to_csv(
        os.path.join(res_dir, "w_hm3.snplist"), sep="\t", index=False)
    for pop in LD_POPULATIONS:
        l2 = rng.gamma(2.0, 10.0, len(panel)) + hot * rng.gamma(4.0, 40.0, len(panel))
        ld = pd.DataFrame({"CHR": panel["chr"].astype(int).to_numpy(), "SNP": panel["rsid"].to_numpy(),
                           "BP": panel["pos38"].astype(int).to_numpy(), "L2": np.round(l2, 3)})
        ld.to_csv(os.path.join(res_dir, f"UKBB.{pop}.l2.ldscore.gz"), sep="\t", index=False, compression="gzip")
        f = panel[f"af_{LD_FREQ[pop]}"]
        with open(os.path.join(res_dir, f"UKBB.{pop}.l2.M_5_50"), "w") as fh:
            fh.write(f"{int(((f > 0.05) & (f < 0.95)).sum())}\n")


# --------------------------------------------------------------------------------------
# Reference genomes, dbSNP and allele frequencies
# --------------------------------------------------------------------------------------
FASTA_LINE = 60
_BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")


def _bgzf_block(data: bytes) -> bytes:
    c = zlib.compressobj(1, zlib.DEFLATED, -15)
    body = c.compress(data) + c.flush()
    header = struct.pack("<BBBBIBBHBBHH", 31, 139, 8, 4, 0, 0, 255, 6, 66, 67, 2, len(body) + 25)
    return header + body + struct.pack("<II", zlib.crc32(data) & 0xFFFFFFFF, len(data))


def write_fasta(path, contigs):
    """A bgzipped FASTA (+ .fai and .gzi, as `samtools faidx` makes) of N except at the given bases.
    contigs: [(name, length, {1-based position: base})]. Full-length chromosomes stay a few MB because every
    all-N block compresses to the same bytes, which are written once and repeated."""
    width = FASTA_LINE + 1
    payload = width * 1070  # whole lines per block, so every all-N block is identical
    n_data = (b"N" * FASTA_LINE + b"\n") * 1070
    n_block = _bgzf_block(n_data)
    fai, gzi = [], []
    coff = uoff = 0
    with open(path, "wb") as out:
        def emit(data, block=None):
            nonlocal coff, uoff
            block = block or _bgzf_block(data)
            if uoff:
                gzi.append((coff, uoff))
            out.write(block)
            coff, uoff = coff + len(block), uoff + len(data)

        for name, length, bases in contigs:
            emit(f">{name}\n".encode())
            fai.append((name, length, uoff))
            nbytes = length + (length + FASTA_LINE - 1) // FASTA_LINE
            edits = sorted(((pos - 1) + (pos - 1) // FASTA_LINE, ord(b)) for pos, b in bases.items())
            k = 0
            for start in range(0, nbytes, payload):
                size = min(payload, nbytes - start)
                lo = k
                while k < len(edits) and edits[k][0] < start + size:
                    k += 1
                if size == payload and lo == k:
                    emit(n_data, n_block)
                    continue
                arr = np.full(size, ord("N"), dtype=np.uint8)
                arr[(np.arange(start, start + size) % width) == FASTA_LINE] = 10
                if start + size == nbytes:
                    arr[-1] = 10
                for off, b in edits[lo:k]:
                    arr[off - start] = b
                emit(arr.tobytes())
        out.write(_BGZF_EOF)
    with open(path + ".fai", "w") as f:
        for name, length, offset in fai:
            f.write(f"{name}\t{length}\t{offset}\t{FASTA_LINE}\t{width}\n")
    with open(path + ".gzi", "wb") as f:
        f.write(struct.pack("<Q", len(gzi)))
        for c, u in gzi:
            f.write(struct.pack("<QQ", c, u))


FLANK = 30


def reference_bases(variants, build, q_sizes, seed=7):
    """{chr: {pos: base}} so that each variant's reference allele is in the genome of `build`, with FLANK random bases
    either side (the same sequence in both builds) so indels have real context to normalise against. GRCh38 forward
    alleles at pos38; GRCh37 at pos37, reverse-complemented in inverted segments (indels there are left out and fail
    harmonisation, as they would liftover). Alleles overlapping one already written are left out; flanks never
    overwrite an allele."""
    rng = np.random.default_rng(seed)
    out = {c: {} for c in variants["chr"].unique()}
    flanks = []
    for v in variants.itertuples():
        left, right = "".join(rng.choice(list("ACGT"), FLANK)), "".join(rng.choice(list("ACGT"), FLANK))  # GRCh38 strand
        if build == "GRCh38":
            if pd.isna(v.pos38):
                continue
            pos, allele, minus = int(v.pos38), v.ref, False
        else:
            pos, allele, minus = int(v.pos37), v.ref, v.strand37to38 == "-"
            if minus:
                if len(v.ref) > 1 or len(v.alt) > 1:
                    continue
                allele = v.ref.translate(COMP)
                left, right = right[::-1].translate(COMP), left[::-1].translate(COMP)
        bases = out[v.chr]
        span = range(pos, pos + len(allele))
        if any(p in bases for p in span):
            continue
        bases.update(zip(span, allele))
        flanks.append((bases, pos - FLANK, left))
        flanks.append((bases, pos + len(allele), right))
    for bases, start, seq in flanks:
        for p, b in zip(range(start, start + len(seq)), seq):
            if p > 0:
                bases.setdefault(p, b)
    return out


def write_reference_genomes(res_dir, variants, q_sizes):
    chroms = [str(i + 1) for i in range(len(CHR_LEN_37))]
    for build, name, sizes in (("GRCh37", "human_g1k_v37.fasta", dict(zip(chroms, CHR_LEN_37))),
                               ("GRCh38", "Homo_sapiens_assembly38_nochr.fasta", q_sizes)):
        bases = reference_bases(variants, build, q_sizes)
        write_fasta(os.path.join(res_dir, name), [(c, sizes[c], bases.get(c, {})) for c in chroms])


def write_dbsnp(res_dir, variants, q_sizes):
    """dbSNP-like GRCh38 VCF (bgzipped + tabix index) with the variants' rsIDs."""
    import pysam
    panel = variants[variants["pos38"].notna()].copy()
    panel["pos"] = panel["pos38"].astype(int)
    panel = panel.sort_values(["chr", "pos"], key=lambda c: c.astype(int) if c.name == "chr" else c)
    plain = os.path.join(res_dir, "dbSNP157_b38_clean.vcf")
    with open(plain, "w") as f:
        f.write("##fileformat=VCFv4.2\n##source=SYNTHETIC dbSNP-like file for GWASHub local development\n")
        for c in sorted(q_sizes, key=int):
            f.write(f"##contig=<ID={c},length={q_sizes[c]}>\n")
        f.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        for v in panel.itertuples():
            f.write(f"{v.chr}\t{v.pos}\t{v.rsid}\t{v.ref}\t{v.alt}\t.\t.\t.\n")
    pysam.tabix_index(plain, preset="vcf", force=True)  # -> .vcf.gz + .vcf.gz.tbi


def write_allele_frequencies(path, variants):
    """1000 Genomes-like ALT allele frequencies per super-population (GRCh38), in the format resources/build/kg1000_b38.sh
    makes and gwashub/eaf_check reads."""
    panel = variants[variants["pos38"].notna()].copy()
    panel["pos_b38"] = panel["pos38"].astype(int)
    panel["id"] = panel["chr"] + ":" + panel["pos_b38"].astype(str) + ":" + panel["ref"] + ":" + panel["alt"]
    for anc in ANCESTRIES:
        panel[f"{anc.lower()}_afreq"] = panel[f"af_{anc}"]
    cols = ["id", "chr", "pos_b38", "ref", "alt"] + [f"{a.lower()}_afreq" for a in ANCESTRIES]
    panel[cols].to_csv(path, sep="\t", index=False, compression="gzip")


def write_genes(rng, path, variants, q_sizes, per_chr=12):
    """GENCODE-like genes (GRCh38) in the format resources/build/genes_gencode.sh makes: a few per chromosome, with
    some placed over the hotspots where simulated signals sit, so locus plots have a gene track."""
    rows, k = [], 0
    hot = variants[variants["hotspot"] & variants["pos38"].notna()]
    for c in [str(i) for i in range(1, 23)]:
        size = q_sizes[c]
        centres = list(rng.integers(1_000_000, size - 1_000_000, per_chr))
        centres += list(hot.loc[hot["chr"] == c, "pos38"].astype(int).drop_duplicates().iloc[::40])
        for centre in sorted(centres):
            k += 1
            length = int(rng.integers(5_000, 150_000))
            start = max(1, int(centre) - length // 2)
            n_ex = int(rng.integers(2, 12))
            cuts = np.sort(rng.choice(np.arange(start, start + length, 100), 2 * n_ex, replace=False))
            exons = ",".join(f"{a}-{b}" for a, b in zip(cuts[0::2], cuts[1::2]))
            gtype = "protein_coding" if rng.random() < 0.7 else "lncRNA"
            rows.append((c, start, start + length, "+" if rng.random() < 0.5 else "-", f"ENSGSYN{k:06d}", f"SYN{k}", gtype, exons))
    pd.DataFrame(rows, columns=["chr", "start", "end", "strand", "gene_id", "gene_name", "gene_type", "exons"]).to_csv(
        path, sep="\t", index=False, compression="gzip")


PANEL_POPS = ("AFR", "AMR", "EAS", "EUR", "SAS")


def panel_haplotypes(rng, variants, pop, n_samples, block_bp=100_000, n_founders=4, n_patterns=3, shared=0.7, mutation=0.01):
    """Phased haplotypes (2 × n_samples, variants) with realistic LD. Within each ~100 kb block every haplotype copies one
    of a few founder haplotypes; most variants (`shared`) follow one of a few allele patterns across the founders, so
    variants on the same pattern are in strong LD (r² near 1) and others partial; the rest take independent founder
    alleles from their frequency. Blocks are independent."""
    af = variants[f"af_{pop}"].to_numpy()
    block = (variants["chr"].astype(int).to_numpy() * 10_000 + variants["pos38"].to_numpy() // block_bp).astype(int)
    _, block_idx = np.unique(block, return_inverse=True)
    n_blocks, n_var = block_idx.max() + 1, len(variants)
    patterns = rng.random((n_blocks, n_patterns, n_founders)) < 0.5  # allele pattern across founders, per block
    patterns[:, :, 0] = ~patterns[:, :, 1:].all(axis=2)  # avoid all-same patterns (monomorphic)
    pick = rng.integers(0, n_patterns, n_var)
    flip = rng.random(n_var) < 0.5
    founder_alleles = (patterns[block_idx, pick].T ^ flip)  # (n_founders, n_var)
    independent = rng.random(n_var) >= shared
    founder_alleles[:, independent] = rng.random((n_founders, int(independent.sum()))) < af[independent]
    choice = rng.integers(0, n_founders, (2 * n_samples, n_blocks))  # which founder each haplotype copies, per block
    haps = founder_alleles[choice[:, block_idx], np.arange(n_var)]
    return (haps ^ (rng.random(haps.shape) < mutation)).astype(np.uint8)


def write_ld_panels(rng, res_dir, variants, n_samples=60):
    """1000 Genomes-like LD reference panels (1kg_b38_<POP>.pgen/.pvar.zst/.psam), built from a synthetic VCF with
    the same PLINK 2 commands as resources/build/kg1000_b38.sh. Needs Docker and the gwashub/base image; without them
    the panels are skipped (meta-analysis lead loci then fall back to a distance window)."""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("docker") or subprocess.run(["docker", "image", "inspect", "gwashub/base"], capture_output=True).returncode:
        print("synth: no Docker image gwashub/base; skipping the LD panels")
        return
    panel = variants[variants["pos38"].notna()].copy()
    panel["pos38"] = panel["pos38"].astype(int)
    panel = panel.sort_values(["chr", "pos38"], key=lambda c: c.astype(int) if c.name == "chr" else c).reset_index(drop=True)
    tmp = tempfile.mkdtemp(prefix="synth_panels_", dir=res_dir)
    samples, gts = [], []
    for pop in PANEL_POPS:
        haps = panel_haplotypes(rng, panel, pop, n_samples)
        gts.append(np.char.add(np.char.add(haps[0::2].T.astype(str), "|"), haps[1::2].T.astype(str)))
        samples += [(f"{pop}{i:03d}", pop) for i in range(n_samples)]
    gt = np.concatenate(gts, axis=1)
    with open(os.path.join(tmp, "panel.vcf"), "w") as f:
        f.write("##fileformat=VCFv4.2\n##source=SYNTHETIC LD reference panel for GWASHub local development\n")
        f.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
        for c in sorted(panel["chr"].unique(), key=int):
            f.write(f"##contig=<ID={c}>\n")
        f.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(s for s, _ in samples) + "\n")
        for v, row in zip(panel.itertuples(), gt):
            f.write(f"{v.chr}\t{v.pos38}\t.\t{v.ref}\t{v.alt}\t.\t.\t.\tGT\t" + "\t".join(row) + "\n")
    with open(os.path.join(tmp, "panel.psam"), "w") as f:
        f.write("#IID\tSEX\tSuperPop\n" + "".join(f"{s}\t2\t{p}\n" for s, p in samples))
    res = os.path.realpath(res_dir)
    tmp = os.path.realpath(tmp)
    cmd = f"""set -e
plink2 --vcf panel.vcf --psam panel.psam --make-pgen vzs --out all >/dev/null
plink2 --pfile all vzs --keep-founders --chr 1-22,X --max-alleles 2 --output-chr MT --set-all-var-ids @:#:\\$r:\\$a \\
       --new-id-max-allele-len 1000 missing --rm-dup exclude-all --make-pgen vzs --out founders >/dev/null
for pop in {' '.join(PANEL_POPS)}; do
  plink2 --pfile founders vzs --keep-if SuperPop==$pop --mac 1 --make-pgen vzs --out {res}/1kg_b38_$pop >/dev/null
done"""
    proc = subprocess.run(["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}", "-v", f"{res}:{res}", "-w", tmp,
                           "--entrypoint", "bash", "gwashub/base", "-c", cmd], capture_output=True, text=True)
    shutil.rmtree(tmp)
    for pop in PANEL_POPS:
        for ext in (".log",):
            if os.path.exists(os.path.join(res, f"1kg_b38_{pop}{ext}")):
                os.remove(os.path.join(res, f"1kg_b38_{pop}{ext}"))
    if proc.returncode:
        raise RuntimeError(f"building the synthetic LD panels failed: {proc.stderr[-1500:]}")


# --------------------------------------------------------------------------------------
# Traits and summary statistics
# --------------------------------------------------------------------------------------
def make_trait(rng, variants, n_loci=18, effect_sd=0.07):
    """True per-variant log-OR: causal variants + decaying LD-ish neighbours."""
    beta = np.zeros(len(variants))
    hot = np.where(variants["hotspot"].to_numpy() & variants["pos38"].notna().to_numpy())[0]
    pool = hot if len(hot) >= n_loci * 5 else np.arange(len(variants))
    causal = []
    for j in rng.permutation(pool):  # one causal variant per hotspot
        if all(variants["chr"].iat[j] != variants["chr"].iat[k] or abs(variants["pos37"].iat[j] - variants["pos37"].iat[k]) > 1_000_000 for k in causal):
            causal.append(j)
        if len(causal) == n_loci:
            break
    causal = np.array(causal)
    chr_arr = variants["chr"].to_numpy()
    pos = variants["pos37"].to_numpy()
    for c in causal:
        b = rng.normal(0, effect_sd)
        b = np.sign(b) * max(abs(b), 0.03)
        near = np.where((chr_arr == chr_arr[c]) & (np.abs(pos - pos[c]) < 400_000))[0]
        for j in near:
            r = np.exp(-abs(pos[j] - pos[c]) / 40_000) * (1 if j == c else rng.uniform(0.6, 1.0))
            beta[j] += b * r * (1 if rng.random() < 0.85 or j == c else -1)
    return beta, variants.index[causal].tolist()


@dataclass
class Cohort:
    name: str
    cohort: str
    phenotype: str
    ancestry: str
    sex: str
    n_cases: int
    n_controls: int
    build: str = "GRCh38"
    style: str = "plink"
    effect_type: str = "beta"
    compress: bool = True
    delimiter: str = "\t"
    coverage: float = 0.93
    inflation: float = 1.0
    faults: list = field(default_factory=list)
    seed: int = 0


STYLES = {
    # standard -> column name, per software style
    "plink": dict(rsid="SNP", chr="CHR", pos="BP", ea="A1", oa="A2", eaf="FRQ", beta="BETA", se="SE", p="P", n="N", info="INFO"),
    "ssf": dict(rsid="variant_id", chr="chromosome", pos="base_pair_location", ea="effect_allele", oa="other_allele",
                eaf="effect_allele_frequency", beta="beta", se="standard_error", p="p_value", n="n"),
    "metal": dict(rsid="MarkerName", chr="Chrom", pos="Pos", ea="Allele1", oa="Allele2", eaf="Freq1", beta="Effect",
                  se="StdErr", p="P-value", n="TotalN"),
    "regenie": dict(rsid="ID", chr="CHROM", pos="GENPOS", ea="ALLELE1", oa="ALLELE0", eaf="A1FREQ", beta="BETA", se="SE",
                    p="P", n="N", info="INFO"),
    "custom": dict(rsid="snpid", chr="chrom", pos="position", ea="EffectAllele", oa="NonEffectAllele", eaf="EAF",
                   beta="logOR", se="se_logOR", p="pval", n="Neff"),
}


def simulate(cohort: Cohort, variants, true_beta):
    rng = np.random.default_rng(cohort.seed)
    keep = rng.random(len(variants)) < cohort.coverage
    v = variants[keep].copy()
    b_true = true_beta[keep]
    f = v[f"af_{cohort.ancestry}"].to_numpy()
    n_eff = 4 / (1 / cohort.n_cases + 1 / cohort.n_controls)
    se = 1 / np.sqrt(n_eff * 2 * f * (1 - f) * 0.25 * 4)  # rough log-OR SE
    beta = rng.normal(b_true, se)
    if cohort.inflation != 1.0:  # inflate test statistics around the truth
        beta = b_true + (beta - b_true) * np.sqrt(cohort.inflation)
    z = beta / se
    p = 2 * stats.norm.sf(np.abs(z))
    eaf_obs = np.clip(rng.binomial(2 * (cohort.n_cases + cohort.n_controls), f) / (2 * (cohort.n_cases + cohort.n_controls)), 0.0005, 0.9995)

    # Effect allele = alt (GRCh38 forward). Randomly report half the variants with ea=ref.
    swap = rng.random(len(v)) < 0.5
    ea = np.where(swap, v["ref"], v["alt"])
    oa = np.where(swap, v["alt"], v["ref"])
    beta = np.where(swap, -beta, beta)
    eaf_obs = np.where(swap, 1 - eaf_obs, eaf_obs)

    if cohort.build == "GRCh37":
        pos = v["pos37"].to_numpy()
        # Alleles are reported on the GRCh37 forward strand: reverse-complemented in inverted segments.
        minus = (v["strand37to38"] == "-").to_numpy()
        ea = np.where(minus, [a.translate(COMP)[::-1] for a in ea], ea)  # reverse complement
        oa = np.where(minus, [a.translate(COMP)[::-1] for a in oa], oa)
    else:
        mapped = v["pos38"].notna().to_numpy()
        v, beta, se, p, ea, oa, eaf_obs = v[mapped], beta[mapped], se[mapped], p[mapped], ea[mapped], oa[mapped], eaf_obs[mapped]
        pos = v["pos38"].astype(int).to_numpy()

    n_total = cohort.n_cases + cohort.n_controls
    out = pd.DataFrame({
        "rsid": v["rsid"].to_numpy(), "chr": v["chr"].to_numpy(), "pos": pos, "ea": ea, "oa": oa,
        "eaf": np.round(eaf_obs, 4), "beta": np.round(beta, 5), "se": np.round(se, 5),
        "p": [f"{x:.4g}" for x in p], "n": n_total,
        "info": np.round(np.clip(rng.beta(12, 1, len(v)), 0.3, 1), 3),
    })
    return apply_faults(out, cohort, rng)


def apply_faults(df, cohort, rng):
    faults = set(cohort.faults)
    if cohort.effect_type == "or":
        df["beta"] = np.round(np.exp(df["beta"].astype(float)), 5)
    if "neglog10p" in faults:
        df["p"] = [f"{-np.log10(float(x)):.4g}" for x in df["p"]]
    if "swapped_allele_columns" in faults:
        df["ea"], df["oa"] = df["oa"].copy(), df["ea"].copy()
    if "duplicates" in faults:
        df = pd.concat([df, df.sample(frac=0.01, random_state=1)])
    if "missing_values" in faults:
        idx = rng.choice(len(df), int(0.012 * len(df)), replace=False)
        df.iloc[idx, df.columns.get_loc("se")] = np.nan
    if "chr_prefix" in faults:
        df["chr"] = "chr" + df["chr"].astype(str)
    if "lowercase_alleles" in faults:
        df["ea"], df["oa"] = df["ea"].str.lower(), df["oa"].str.lower()
    if "drop_se" in faults:
        df = df.drop(columns=["se"])
    return df.sort_values(["chr", "pos"])


def write_dataset(df, cohort: Cohort, out_dir):
    cols = STYLES[cohort.style]
    df = df[[c for c in cols if c in df.columns]].rename(columns=cols)
    ext = {"\t": "tsv", ",": "csv", " ": "txt"}[cohort.delimiter]
    fname = f"{cohort.name}.{ext}" + (".gz" if cohort.compress else "")
    path = os.path.join(out_dir, fname)
    df.to_csv(path, sep=cohort.delimiter, index=False, compression="gzip" if cohort.compress else None)
    mapping = {std: col for std, col in cols.items() if col in df.columns}
    return fname, mapping


# --------------------------------------------------------------------------------------
# Demo set
# --------------------------------------------------------------------------------------
def demo_cohorts():
    C = Cohort
    return [
        # HERMES-like heart failure cohorts: mostly clean, varied formats/builds.
        C("ukb_hf_eur", "UK Biobank", "All-cause HF", "EUR", "Combined", 12000, 380000, "GRCh37", "plink", seed=1),
        C("finngen_hf_eur", "FinnGen", "All-cause HF", "EUR", "Combined", 9000, 250000, "GRCh38", "regenie", seed=2),
        C("mvp_hf_afr", "MVP", "All-cause HF", "AFR", "Combined", 5200, 60000, "GRCh38", "ssf", seed=3),
        C("bbj_hf_eas", "BBJ", "All-cause HF", "EAS", "Combined", 4100, 150000, "GRCh37", "metal", effect_type="or",
          delimiter=",", compress=False, seed=4),
        C("estbb_hf_eur_f", "EstBB", "All-cause HF", "EUR", "Female", 2600, 70000, "GRCh38", "plink", seed=5,
          faults=["missing_values", "duplicates", "chr_prefix"]),
        C("hunt_hf_eur", "HUNT", "All-cause HF", "EUR", "Combined", 3900, 60000, "GRCh37", "custom", inflation=1.18,
          faults=["lowercase_alleles"], seed=6),
        C("estbb_hf_eur_m", "EstBB", "All-cause HF", "EUR", "Male", 3100, 62000, "GRCh38", "plink", seed=14),
        # Broken submissions the QC should catch.
        C("biovu_hf_eur_neglog10p", "BioVU", "All-cause HF", "EUR", "Combined", 3000, 50000, "GRCh38", "plink",
          faults=["neglog10p"], seed=7),
        C("partners_hf_eur_swapped", "Partners Biobank", "All-cause HF", "EUR", "Combined", 2800, 40000, "GRCh38", "ssf",
          faults=["swapped_allele_columns"], seed=8),
        C("decode_hf_eur_wrongbuild", "deCODE", "All-cause HF", "EUR", "Combined", 5000, 200000, "GRCh37", "plink",
          seed=9),  # declared as GRCh38 in the manifest below
        C("gnh_hf_sas_nose", "Genes & Health", "All-cause HF", "SAS", "Combined", 1500, 40000, "GRCh38", "plink",
          faults=["drop_se"], seed=10),
        # HCM consortium: a second trait with its own causal loci.
        C("share_hcm_eur", "SHaRe", "HCM", "EUR", "Combined", 6000, 60000, "GRCh38", "ssf", seed=11),
        C("hcmr_hcm_eur", "HCMR", "HCM", "EUR", "Combined", 2700, 40000, "GRCh37", "plink", seed=12),
        C("pk_hcm_sas", "Pakistan HCM", "HCM", "SAS", "Combined", 1800, 20000, "GRCh38", "custom", seed=13),
    ]


def generate(out, n_variants=40000, small=False, seed=20261002):
    rng = np.random.default_rng(seed)
    res_dir, ds_dir = os.path.join(out, "resources"), os.path.join(out, "fake_gwas")
    os.makedirs(res_dir, exist_ok=True)
    os.makedirs(ds_dir, exist_ok=True)
    if small:
        n_variants = 3000
        hotspots = 8
    else:
        hotspots = 30
    segments, q_sizes = make_chain(rng)
    write_chain(os.path.join(res_dir, "hg19ToHg38.over.chain.gz"), segments, q_sizes)
    variants = make_variants(rng, n_variants, segments, q_sizes, n_hotspots=hotspots)
    write_reference_genomes(res_dir, variants, q_sizes)
    write_dbsnp(res_dir, variants, q_sizes)
    write_allele_frequencies(os.path.join(res_dir, "all_afreq_b38.tsv.gz"), variants)
    write_ldsc_reference(rng, res_dir, variants)
    write_ld_panels(np.random.default_rng(seed + 1), res_dir, variants, n_samples=40 if small else 100)
    write_genes(np.random.default_rng(seed + 2), os.path.join(res_dir, "genes_b38.tsv.gz"), variants, q_sizes)
    variants.to_csv(os.path.join(out, "truth_variants.tsv.gz"), sep="\t", index=False)
    traits, truth = {}, []
    for pheno, n_loci in (("All-cause HF", 6 if small else 20), ("HCM", 3 if small else 8)):
        tb, causal = make_trait(rng, variants, n_loci=n_loci)
        traits[pheno] = tb
        truth.append(pd.DataFrame({"phenotype": pheno, "variant": variants.loc[causal, "rsid"], "chr": variants.loc[causal, "chr"],
                                   "pos38": variants.loc[causal, "pos38"], "beta": tb[causal]}))
    pd.concat(truth).to_csv(os.path.join(out, "truth_causal.tsv"), sep="\t", index=False)

    manifest = []
    cohorts = demo_cohorts()
    if small:
        cohorts = [c for c in cohorts if c.name in ("ukb_hf_eur", "finngen_hf_eur", "bbj_hf_eas")]
        for c in cohorts:
            c.n_cases, c.n_controls = c.n_cases * 20, c.n_controls * 5  # strong signal in tiny data
    for c in cohorts:
        df = simulate(c, variants, traits[c.phenotype])
        fname, mapping = write_dataset(df, c, ds_dir)
        declared_build = "GRCh38" if c.name.endswith("wrongbuild") else c.build
        manifest.append({
            "file": fname, "cohort": c.cohort, "phenotype": c.phenotype, "ancestry": c.ancestry, "sex": c.sex,
            "n_cases": c.n_cases, "n_controls": c.n_controls, "build": declared_build,
            "effect_type": c.effect_type, "column_mapping": mapping, "faults": c.faults,
            "true_build": c.build, "inflation": c.inflation, "rows": len(df),
        })
    with open(os.path.join(out, "fake_gwas", "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="local/synthetic")
    ap.add_argument("--variants", type=int, default=40000)
    ap.add_argument("--small", action="store_true")
    a = ap.parse_args()
    m = generate(a.out, a.variants, a.small)
    for d in m:
        print(f"{d['file']:<40} {d['rows']:>7} rows  {d['ancestry']}  {d['build']}  faults={d['faults']}")


if __name__ == "__main__":
    main()


PARAM_KEYS = ("column_mapping", "build", "ancestry", "effect_type", "n_cases", "n_controls", "cohort", "phenotype", "sex")


def params_for(entry):
    """Run params for a manifest entry (what the upload form would submit)."""
    return {k: entry[k] for k in PARAM_KEYS}
