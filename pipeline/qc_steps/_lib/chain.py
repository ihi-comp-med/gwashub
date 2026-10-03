"""Minimal UCSC chain-file liftover (position + strand), vectorised with numpy.

Works with real chain files (e.g. hg19ToHg38.over.chain.gz). Where alignment blocks overlap,
the block from the higher-scoring chain wins.
"""
import numpy as np

from _lib.common import open_text


class Chain:
    def __init__(self, path):
        blocks = []  # (t_chr, t_start, t_end, q_chr, q_start_strand, strand, q_size, score)
        with open_text(path) as f:
            header = None
            t = q = 0
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("chain"):
                    h = line.split()
                    header = dict(score=float(h[1]), t_chr=h[2], q_chr=h[7], q_size=int(h[8]), strand=h[9])
                    t, q = int(h[5]), int(h[10])
                    continue
                parts = [int(x) for x in line.split()]
                size = parts[0]
                blocks.append((header["t_chr"], t, t + size, header["q_chr"], q, header["strand"], header["q_size"], header["score"]))
                if len(parts) == 3:
                    t += size + parts[1]
                    q += size + parts[2]
        self.by_chr = {}
        for b in sorted(blocks, key=lambda b: (b[0], b[1])):
            kept = self.by_chr.setdefault(_norm(b[0]), [])
            if kept and b[1] < kept[-1][2]:  # overlap: keep the higher score
                if b[7] > kept[-1][7]:
                    kept[-1] = b
                continue
            kept.append(b)
        self.arrays = {c: (np.array([b[1] for b in bl]), np.array([b[2] for b in bl])) for c, bl in self.by_chr.items()}

    def lift(self, chrom, pos):
        """Lift 1-based positions on one chromosome. Returns (q_chr, q_pos, strand), NaN/None where unmapped."""
        pos = np.asarray(pos, dtype=np.int64)
        n = len(pos)
        out_chr = np.full(n, None, dtype=object)
        out_pos = np.full(n, -1, dtype=np.int64)
        out_strand = np.full(n, None, dtype=object)
        key = _norm(chrom)
        if key not in self.arrays:
            return out_chr, out_pos, out_strand
        starts, ends = self.arrays[key]
        blocks = self.by_chr[key]
        t = pos - 1
        idx = np.searchsorted(starts, t, side="right") - 1
        valid = (idx >= 0) & (t < ends[np.clip(idx, 0, None)])
        for i in np.nonzero(valid)[0]:
            _, t0, _, q_chr, q0, strand, q_size, _ = blocks[idx[i]]
            qs = q0 + (t[i] - t0)
            out_pos[i] = (qs if strand == "+" else q_size - 1 - qs) + 1
            out_chr[i] = _norm(q_chr)
            out_strand[i] = strand
        return out_chr, out_pos, out_strand


def _norm(c):
    c = str(c)
    return c[3:] if c.lower().startswith("chr") else c
