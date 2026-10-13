"""The Skin Genetics Consortium's code in GWASHub: their files are copied unchanged (each folder's SOURCE.md), and the
GWASHub code around them changes nothing they do."""
import hashlib
import os
import re

import numpy as np

from _lib.testing import needs_image
from pipeline.tests.test_meta_analysis import run_meta, study

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SGC_FOLDERS = ["pipeline/qc_steps/sgc/qc_munge", "pipeline/images/sgc/mungesumstats", "pipeline/analyses/meta_analysis/sgc/metal"]


def recorded(folder):
    """{file: (SGC's sha256, sha256 here)} from the table in a folder's SOURCE.md."""
    with open(os.path.join(ROOT, folder, "SOURCE.md")) as f:
        rows = re.findall(r"^\| `([^`]+)` \| \S+ \| `[0-9a-f]{40}` \| `([0-9a-f]{64})` \| `([0-9a-f]{64})` \|$", f.read(), re.M)
    return {name: (theirs, ours) for name, theirs, ours in rows}


def test_sgc_files_match_their_record():
    """Every file SOURCE.md lists matches the checksum recorded "here": SGC's file, or SGC's file with the edits that
    SOURCE.md lists (each marked in the file). A file with no edits has both checksums the same."""
    files = {}
    for folder in SGC_FOLDERS:
        rec = recorded(folder)
        assert rec, f"{folder}/SOURCE.md lists no files"
        for name, (theirs, ours) in rec.items():
            with open(os.path.join(ROOT, folder, name), "rb") as f:
                text = f.read()
            assert hashlib.sha256(text).hexdigest() == ours, f"{folder}/{name} has changed since SOURCE.md was written"
            if theirs != ours:  # edited: each edit marked in the file and listed in SOURCE.md
                with open(os.path.join(ROOT, folder, "SOURCE.md")) as f:
                    listed = len(re.findall(r"^\d+\. \*\*", f.read().split("## GWASHub edits")[1], re.M))
                marked = set(re.findall(rb"GWASHub edit (\d+)", text))
                assert listed and len(marked) == listed, f"{folder}/{name}: edits listed {listed}, marked {sorted(marked)}"
            files[name] = (theirs, ours)
    assert {"qc_munge.R", "qc_setup.R", "METAL_example_single.metal"} <= set(files)
    for name in ("qc_setup.R", "METAL_example_single.metal"):  # used exactly as SGC wrote them
        assert files[name][0] == files[name][1], name


def test_sgc_steps_and_method_load():
    from pipeline.runner.catalog import load_analyses, load_images, load_steps
    steps = load_steps()
    assert steps["sgc/qc_munge"].image == steps["sgc/check_build"].image == "sgc/mungesumstats"
    assert "sgc/mungesumstats" in load_images() and load_analyses()["sgc/metal"].image == "gwashub/metal"


@needs_image("gwashub/metal")
def test_sgc_metal_runs_sgc_script_with_only_its_datasets_set(tmp_path):
    """sgc/metal runs SGC's METAL script as written, with only its PROCESS and OUTFILE lines set for the run. The
    effects agree with gwashub/metal (METAL too, the same scheme) to the 4 significant digits SGC's script prints;
    cases and controls are summed across datasets (SGC's CUSTOMVARIABLE)."""
    a = study([0.2, 0.05], [0.1, 0.1]).assign(ncase=100, ncontrol=900)
    b = study([-0.1, 0.03], [0.05, 0.1], ea="G", oa="A", eaf=0.7).assign(ncase=50, ncontrol=450)
    sgc, _, out, result = run_meta(tmp_path / "sgc", [a, b], method="sgc/metal")
    ours, _, _, _ = run_meta(tmp_path / "ours", [a, b], {"minmax_freq": True, "min_studies": 1}, method="gwashub/metal")
    with open(os.path.join(ROOT, SGC_FOLDERS[2], "METAL_example_single.metal")) as f:
        original = [ln for ln in f.read().splitlines() if not re.match(r"\s*(PROCESS|OUTFILE)\b", ln)]
    ran = (out / "sgc_metal_script.txt").read_text().splitlines()
    assert [ln for ln in ran if not re.match(r"\s*(PROCESS|OUTFILE)\b", ln)] == original
    assert [ln.split()[1] for ln in ran if ln.startswith("PROCESS")] == ["dataset_1.tsv", "dataset_2.tsv"]
    s, o = sgc.set_index("pos"), ours.set_index("pos")
    for col in ("beta", "se"):
        assert np.allclose(s[col].abs(), o.loc[s.index, col].abs(), rtol=5e-4), col
    assert (s["ncase"] == 150).all() and (s["ncontrol"] == 1350).all() and (s["n"] == 1500).all()
    assert {"eaf_min", "eaf_max", "q", "i2"} <= set(sgc.columns)
    assert {f["path"] for f in result["log"]["files"]} >= {"sgc_metal_script.txt", "metal_console.txt"}
    assert "Ncases, Ncontrols summed" in result["log"]["messages"][0]
