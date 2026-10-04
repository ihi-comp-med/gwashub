import json
import os

import pandas as pd
import pytest

from pipeline.runner.catalog import build_order, load_images, load_steps
from pipeline.runner.executor import run_pipeline
from pipeline.runner.pipeline import Entry, entries_from_dicts, load_template, plan_groups, resolve

from devtools.synth import params_for

DEFAULT = load_template("pipeline/qc_steps/templates/default.yaml")


def run(synth, entry, out, entries=DEFAULT, catalog=None, params=None, resources=None):
    resolved = resolve(entries, catalog or load_steps())
    return run_pipeline(resolved, os.path.join(synth["dir"], "fake_gwas", entry["file"]),
                        params or params_for(entry), resources or synth["resources"], str(out))


def test_default_pipeline_end_to_end(synth_small, tmp_path):
    entry = synth_small["manifest"][0]  # GRCh37, plink style
    res = run(synth_small, entry, tmp_path)
    assert res["status"] == "completed", res
    assert res["overall_verdict"] in ("pass", "warn")
    assert [s["state"] for s in res["steps"]].count("skipped") == 0
    out = pd.read_csv(tmp_path / "gwas_qc.tsv", sep="\t", dtype={"chr": str})
    assert {"chr", "pos", "ea", "oa", "beta", "se", "p"} <= set(out.columns)
    assert len(out) > 0.9 * entry["rows"]
    assert (tmp_path / res["outputs"]["qc_report"]).exists()
    plots = next(s["dir"] for s in res["steps"] if s["key"] == "gwashub/plots")
    assert (tmp_path / plots / "manhattan.png").exists()
    # Every step only saw its declared resources.
    assert os.listdir(tmp_path / "resources" / "05") == ["hg19ToHg38.over.chain.gz"]
    assert os.listdir(tmp_path / "resources" / "01") == []
    # The runner profiles the upload and the cleaned file whatever the pipeline, and counts rows per step.
    before, after = res["profile"]["input"], res["profile"]["output"]
    assert before["rows"] == entry["rows"] and after["rows"] == len(out)
    assert {"beta", "se", "p"} <= set(before["columns"]) and after["columns"]["p"]["max"] <= 1
    assert res["steps"][0]["rows_before"] == entry["rows"]
    assert all(s["rows_before"] == prev["rows_after"] for prev, s in zip(res["steps"], res["steps"][1:]))
    assert json.loads((tmp_path / "profile_input.json").read_text()) == before


def test_or_dataset_is_converted(synth_small, tmp_path):
    entry = next(e for e in synth_small["manifest"] if e["effect_type"] == "or")
    res = run(synth_small, entry, tmp_path)
    assert res["status"] == "completed"
    harm = next(s for s in res["steps"] if s["key"] == "gwashub/harmonise_columns")
    assert harm["metrics"]["or_to_beta"] > 0
    # Uploaded values are profiled as received: odds ratios are positive, BETA after QC is not.
    assert res["profile"]["input"]["columns"]["beta"]["min"] > 0 and res["profile"]["output"]["columns"]["beta"]["min"] < 0


def test_block_stops_pipeline_but_report_still_runs(synth_small, tmp_path):
    entry = dict(synth_small["manifest"][1])
    entry["column_mapping"] = {k: v for k, v in entry["column_mapping"].items() if k != "se"}
    res = run(synth_small, entry, tmp_path)
    assert res["status"] == "blocked" and res["overall_verdict"] == "fail"
    states = {s["key"].split("/")[1]: s["state"] for s in res["steps"]}
    assert states["row_check"] == "fail" and states["liftover"] == "skipped" and states["qc_report"] == "pass"


def test_flag_continues(synth_small, tmp_path):
    entry = dict(synth_small["manifest"][1])
    entry["column_mapping"] = {k: v for k, v in entry["column_mapping"].items() if k != "se"}
    entries = [Entry("gwashub/row_check", on_fail="flag"), Entry("gwashub/qc_report")]
    res = run(synth_small, entry, tmp_path, entries=entries)
    assert res["status"] == "completed" and res["overall_verdict"] == "fail"


def test_missing_resource_fails_run(synth_small, tmp_path):
    empty = tmp_path / "noresources"
    empty.mkdir()
    entry = synth_small["manifest"][0]
    res = run(synth_small, entry, tmp_path / "run", resources=str(empty))
    assert res["status"] == "failed"
    assert "missing resource" in res["stop_reason"]


CUSTOM_PY = '''
import argparse, json
ap = argparse.ArgumentParser()
for f in ("--input", "--resources", "--params", "--log"): ap.add_argument(f)
a = ap.parse_args()
rows = open(a.input).read().splitlines()
open(a.input, "w").write("\\n".join(rows[:101]) + "\\n")  # keep header + 100 rows
json.dump({"step": "keep100", "verdict": "warn", "metrics": {"rows_removed": len(rows) - 101}, "messages": ["kept 100"]}, open(a.log, "w"))
'''


def _owner_steps(root, owner, steps):
    """A consortium folder like pipeline/qc_steps/<owner>/ (what a pull request adds)."""
    for name, (script, body, extra) in steps.items():
        d = root / owner / name
        d.mkdir(parents=True)
        (d / script).write_text(body)
        (d / "step.yaml").write_text(f"name: {name}\ntitle: {name.title()}\ncommand: [python, {script}]\nimage: gwashub/base\n{extra}")
    return load_steps(str(root))


def test_consortium_step_folder(synth_small, tmp_path):
    extra = _owner_steps(tmp_path / "qc_steps", "testco", {"keep100": ("keep100.py", CUSTOM_PY, "default_on_fail: flag\n")})
    assert list(extra) == ["testco/keep100"] and extra["testco/keep100"].owner == "testco"
    entries = entries_from_dicts([{"step": "gwashub/row_check"}, {"step": "gwashub/harmonise_columns"},
                                  {"step": "testco/keep100"}, {"step": "gwashub/qc_report"}])
    res = run(synth_small, synth_small["manifest"][1], tmp_path / "run", entries=entries, catalog={**load_steps(), **extra})
    assert res["status"] == "completed" and res["overall_verdict"] == "warn"
    assert res["steps"][2]["rows_after"] == 100 and res["steps"][2]["source"]["path"].endswith("testco/keep100/keep100.py")


def test_crashing_step_is_reported(synth_small, tmp_path):
    extra = _owner_steps(tmp_path / "qc_steps", "testco", {"boom": ("boom.py", "raise SystemExit('kaboom')", "")})
    entries = entries_from_dicts([{"step": "testco/boom"}, {"step": "gwashub/row_check"}, {"step": "gwashub/qc_report"}])
    res = run(synth_small, synth_small["manifest"][0], tmp_path / "run", entries=entries, catalog={**load_steps(), **extra})
    assert res["status"] == "failed" and "crashed" in res["stop_reason"]
    assert "kaboom" in res["steps"][0]["messages"][1]
    assert [s["state"] for s in res["steps"]] == ["error", "skipped", "pass"]


def test_default_pipeline_is_one_image_group():
    resolved = resolve(DEFAULT, load_steps(), images=load_images())
    assert plan_groups(resolved) == [{"image": "gwashub/base", "steps": list(range(len(resolved)))}]


def test_groups_split_where_the_image_changes():
    entries = entries_from_dicts([{"step": "gwashub/row_check"}, {"step": "gwashub/harmonise_columns"},
                                  {"step": "hcm/sarcomere_filter"}, {"step": "gwashub/plots"}, {"step": "gwashub/qc_report"}])
    resolved = resolve(entries, load_steps(), images=load_images())
    assert plan_groups(resolved) == [{"image": "gwashub/base", "steps": [0, 1]}, {"image": "gwashub/base-r", "steps": [2]},
                                     {"image": "gwashub/base", "steps": [3, 4]}]
    with pytest.raises(ValueError, match="unknown image"):
        resolve(entries_from_dicts([{"step": "gwashub/row_check", "image": "nobody/nothing"}]), load_steps(), images=load_images())


def test_images_build_after_their_base():
    images = load_images()
    assert {"gwashub/base", "gwashub/base-r"} <= set(images)
    order = build_order(images)
    assert order.index("gwashub/base") < order.index("gwashub/base-r")


def test_unknown_step_and_bad_on_fail_rejected():
    with pytest.raises(ValueError):
        resolve([Entry("gwashub/nope")], load_steps())
    with pytest.raises(ValueError):
        resolve([Entry("gwashub/row_check", on_fail="explode")], load_steps())
