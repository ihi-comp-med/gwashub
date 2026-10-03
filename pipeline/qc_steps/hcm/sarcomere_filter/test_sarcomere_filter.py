import json
import shutil
import subprocess

import pytest

HERE = __import__("os").path.dirname(__file__)


@pytest.mark.skipif(not shutil.which("Rscript"), reason="R not installed")
def test_removes_variants_in_regions(tmp_path):
    (tmp_path / "res").mkdir()
    (tmp_path / "res" / "sarcomere_genes_grch38.bed").write_text("chr1\t100\t200\tSARC1\n")
    data = tmp_path / "working.tsv"
    data.write_text("chr\tpos\tea\toa\tbeta\tse\tp\n1\t150\tA\tG\t0.1\t0.01\t0.5\n1\t500\tA\tG\t0.1\t0.01\t0.5\n2\t150\tC\tT\t0.1\t0.01\t0.5\n")
    log = tmp_path / "log.json"
    subprocess.run(["Rscript", f"{HERE}/sarcomere_filter.R", "--input", str(data), "--resources", str(tmp_path / "res"),
                    "--params", str(tmp_path / "params.json"), "--log", str(log)], check=True)
    assert len(data.read_text().strip().splitlines()) == 3  # header + 2 rows left
    out = json.loads(log.read_text())
    assert out["verdict"] == "pass" and out["metrics"]["rows_removed"] == 1
