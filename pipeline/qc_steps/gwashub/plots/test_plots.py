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
