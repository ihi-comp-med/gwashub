import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QC_STEPS = os.path.dirname(os.path.dirname(HERE))


def test_says_hello_and_draws_a_cat(tmp_path):
    data = tmp_path / "working.tsv"
    data.write_text("chr\tpos\tea\toa\tbeta\tse\tp\n1\t100\tA\tG\t0.1\t0.01\t0.5\n")
    before = data.read_text()
    (tmp_path / "params.json").write_text(json.dumps({"build": "GRCh38", "step": {"greeting": "hello world"}}))
    (tmp_path / "res").mkdir()
    env = {**os.environ, "PYTHONPATH": QC_STEPS, "GWASHUB_ARTIFACT_DIR": str(tmp_path / "art"), "MPLCONFIGDIR": str(tmp_path / "mpl")}
    proc = subprocess.run([sys.executable, os.path.join(HERE, "hello_world.py"), "--input", str(data), "--resources", str(tmp_path / "res"),
                           "--params", str(tmp_path / "params.json"), "--log", str(tmp_path / "log.json")],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "hello world"
    log = json.loads((tmp_path / "log.json").read_text())
    assert log["verdict"] == "pass" and log["messages"] == ["The step says: hello world"]
    assert log["artifacts"][0]["path"] == "hello_cat.png"
    png = tmp_path / "art" / "hello_cat.png"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and png.stat().st_size > 5000
    assert data.read_text() == before  # the data is untouched
