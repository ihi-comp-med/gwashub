import json

import numpy as np
import pandas as pd

from _lib.testing import run_step


def test_writes_pngs_and_plot_data(tmp_path):
    rng = np.random.default_rng(0)
    n = 3000
    df = pd.DataFrame({"chr": rng.choice([str(i) for i in range(1, 23)], n), "pos": rng.integers(1, 10**8, n),
                       "ea": "A", "oa": "G", "beta": 0.1, "se": 0.1, "p": rng.uniform(size=n)})
    log, _ = run_step("plots", df, {}, tmp_path)
    art = tmp_path / "artifacts"
    assert log["verdict"] == "pass"
    assert (art / "manhattan.png").stat().st_size > 1000 and (art / "qq.png").exists()
    data = json.loads((art / "plot_data.json").read_text())
    assert len(data["manhattan"]["x"]) == n and 0.8 < data["qq"]["lambda"] < 1.2



def test_qq_points_leave_no_gap():
    """The website's QQ plot gets about 1,500 points: the 500 strongest and the rest spread evenly along the expected
    -log10 P axis. Ranks spread evenly left a gap between the two (about 2.7 to 4.3 for 10 million variants)."""
    from _lib.plotting import qq_points
    for n in (10_000_000, 300_000, 800, 1):
        idx = qq_points(n)
        exp = -np.log10((idx + 0.5) / n)  # expected -log10 P at each chosen rank (0-based), strongest first
        assert len(idx) <= 1500 and idx[0] == 0 and idx[-1] == n - 1
        gaps = -np.diff(exp)
        consecutive = np.diff(idx) == 1  # neighbouring ranks: their gap is the data's own, not the sampling's
        assert (gaps[~consecutive] < 0.02).all(), (n, gaps[~consecutive].max())


def test_png_plots_sample_the_null_band(tmp_path, monkeypatch):
    """With more variants than the PNG draws, every one with P < 0.001 is still drawn and the PNGs are written; the
    interactive plot data and lambda_GC still come from every variant."""
    import _lib.plotting as plotting
    monkeypatch.setattr(plotting, "PNG_NULL_POINTS", 1000)
    rng = np.random.default_rng(2)
    n = 50_000
    df = pd.DataFrame({"chr": rng.choice([str(i) for i in range(1, 23)], n), "pos": rng.integers(1, 10**8, n), "p": rng.uniform(size=n)})
    df.loc[:9, "p"] = 1e-12
    lam, count = plotting.make_plots(df, str(tmp_path))
    assert count == n and 0.9 < lam < 1.1
    assert (tmp_path / "manhattan.png").stat().st_size > 1000 and (tmp_path / "qq.png").stat().st_size > 1000
