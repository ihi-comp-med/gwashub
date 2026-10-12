import json
import os

import pandas as pd
import pytest

from pipeline.runner.catalog import build_order, load_images, load_steps
from pipeline.runner.executor import run_pipeline
from pipeline.runner.pipeline import Entry, entries_from_dicts, load_template, plan_groups, resolve

from _lib.testing import needs_image
from devtools.synth import params_for

DEFAULT = load_template("pipeline/qc_steps/templates/default.yaml")
IMAGES = ("gwashub/base", "gwashub/base-r", "gwashub/vcf", "gwashub/ldsc")


def run(synth, entry, out, entries=DEFAULT, catalog=None, params=None, resources=None):
    resolved = resolve(entries, catalog or load_steps())
    return run_pipeline(resolved, os.path.join(synth["dir"], "fake_gwas", entry["file"]),
                        params or params_for(entry), resources or synth["resources"], str(out))


@needs_image(*IMAGES)
def test_default_pipeline_end_to_end(synth_small, tmp_path):
    entry = synth_small["manifest"][0]  # GRCh37, plink style
    res = run(synth_small, entry, tmp_path)
    assert res["status"] == "completed", res
    assert res["overall_verdict"] in ("pass", "warn")
    assert [s["key"] for s in res["steps"]] == [e.step for e in DEFAULT]
    assert [s["state"] for s in res["steps"]].count("skipped") == 0
    out = pd.read_csv(tmp_path / "gwas_qc.tsv", sep="\t", dtype={"chr": str})
    assert list(out.columns) == ["chr", "pos", "ea", "oa", "beta", "se", "p", "eaf", "n", "ncase", "ncontrol", "rsid", "info"]
    # rsIDs come only from dbSNP: most variants have one, the rest (e.g. indels dbSNP lists differently) have none
    rsid = out["rsid"].dropna()
    assert len(out) > 0.9 * entry["rows"] and rsid.str.startswith("rs").all() and len(rsid) > 0.9 * len(out)
    assert (tmp_path / res["outputs"]["qc_report"]).exists()
    steps = {s["key"]: s for s in res["steps"]}
    assert (tmp_path / steps["gwashub/plots"]["dir"] / "manhattan.png").exists()
    lift = steps["gwashub/harmonise_liftover"]["metrics"]
    assert lift["lifted"] > 0.95 * lift["harmonised"] and lift["strand_flipped"] > 0
    assert steps["gwashub/ldsc"]["metrics"]["ran"] and steps["gwashub/eaf_check"]["metrics"]["eaf_correlation"] > 0.9
    # Every step only saw its declared resources.
    assert os.listdir(tmp_path / "resources" / "01") == []
    assert "hg19ToHg38.over.chain.gz" in os.listdir(tmp_path / "resources" / "03")
    assert os.listdir(tmp_path / "resources" / "04") == ["all_afreq_b38.tsv.gz"]
    assert sorted(os.listdir(tmp_path / "resources" / "05")) == ["UKBB.AFR.l2.M_5_50", "UKBB.AFR.l2.ldscore.gz", "UKBB.AMR.l2.M_5_50",
        "UKBB.AMR.l2.ldscore.gz", "UKBB.CSA.l2.M_5_50", "UKBB.CSA.l2.ldscore.gz", "UKBB.EAS.l2.M_5_50", "UKBB.EAS.l2.ldscore.gz",
        "UKBB.EUR.l2.M_5_50", "UKBB.EUR.l2.ldscore.gz", "UKBB.MID.l2.M_5_50", "UKBB.MID.l2.ldscore.gz", "w_hm3.snplist"]
    # The runner profiles the upload and the cleaned file whatever the pipeline, and counts rows per step.
    before, after = res["profile"]["input"], res["profile"]["output"]
    assert before["rows"] == entry["rows"] and after["rows"] == len(out)
    assert {"beta", "se", "p"} <= set(before["columns"]) and after["columns"]["p"]["max"] <= 1
    assert res["steps"][0]["rows_before"] == entry["rows"]
    assert all(s["rows_before"] == prev["rows_after"] for prev, s in zip(res["steps"], res["steps"][1:]))
    assert json.loads((tmp_path / "profile_input.json").read_text()) == before


@needs_image(*IMAGES)
def test_or_dataset_is_converted(synth_small, tmp_path):
    entry = next(e for e in synth_small["manifest"] if e["effect_type"] == "or")
    res = run(synth_small, entry, tmp_path)
    assert res["status"] == "completed"
    parse = next(s for s in res["steps"] if s["key"] == "gwashub/parse")
    assert parse["metrics"]["or_to_beta"] > 0
    # Uploaded values are profiled as received: odds ratios are positive, BETA after QC is not.
    assert res["profile"]["input"]["columns"]["beta"]["min"] > 0 and res["profile"]["output"]["columns"]["beta"]["min"] < 0


@needs_image(*IMAGES)
def test_block_stops_pipeline_but_report_still_runs(synth_small, tmp_path):
    entry = dict(synth_small["manifest"][1])
    entry["column_mapping"] = {k: v for k, v in entry["column_mapping"].items() if k != "se"}
    res = run(synth_small, entry, tmp_path)
    assert res["status"] == "blocked" and res["overall_verdict"] == "fail"
    states = {s["key"].split("/")[1]: s["state"] for s in res["steps"]}
    assert states["parse"] == "fail" and states["harmonise_liftover"] == "skipped" and states["qc_report"] == "pass"


@needs_image("gwashub/base", "gwashub/base-r")
def test_flag_continues(synth_small, tmp_path):
    entry = dict(synth_small["manifest"][1])
    entry["column_mapping"] = {k: v for k, v in entry["column_mapping"].items() if k != "se"}
    entries = [Entry("gwashub/parse", on_fail="flag"), Entry("gwashub/qc_report")]
    res = run(synth_small, entry, tmp_path, entries=entries)
    assert res["status"] == "completed" and res["overall_verdict"] == "fail"


@needs_image(*IMAGES)
def test_missing_resource_fails_run(synth_small, tmp_path):
    empty = tmp_path / "noresources"
    empty.mkdir()
    entry = synth_small["manifest"][0]
    res = run(synth_small, entry, tmp_path / "run", resources=str(empty))
    assert res["status"] == "failed"
    assert "Harmonise and liftover: missing resource" in res["stop_reason"]


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


@needs_image("gwashub/base", "gwashub/base-r")
def test_consortium_step_folder(synth_small, tmp_path):
    extra = _owner_steps(tmp_path / "qc_steps", "testco", {"keep100": ("keep100.py", CUSTOM_PY, "default_on_fail: flag\n")})
    assert list(extra) == ["testco/keep100"] and extra["testco/keep100"].owner == "testco"
    entries = entries_from_dicts([{"step": "gwashub/parse"}, {"step": "testco/keep100"}, {"step": "gwashub/qc_report"}])
    res = run(synth_small, synth_small["manifest"][1], tmp_path / "run", entries=entries, catalog={**load_steps(), **extra})
    assert res["status"] == "completed" and res["overall_verdict"] == "warn"
    assert res["steps"][1]["rows_after"] == 100 and res["steps"][1]["source"]["path"].endswith("testco/keep100/keep100.py")


@needs_image("gwashub/base", "gwashub/base-r")
def test_crashing_step_is_reported(synth_small, tmp_path):
    extra = _owner_steps(tmp_path / "qc_steps", "testco", {"boom": ("boom.py", "raise SystemExit('kaboom')", "")})
    entries = entries_from_dicts([{"step": "testco/boom"}, {"step": "gwashub/parse"}, {"step": "gwashub/qc_report"}])
    res = run(synth_small, synth_small["manifest"][0], tmp_path / "run", entries=entries, catalog={**load_steps(), **extra})
    assert res["status"] == "failed" and "crashed" in res["stop_reason"]
    assert "kaboom" in res["steps"][0]["messages"][1]
    assert [s["state"] for s in res["steps"]] == ["error", "skipped", "pass"]


def test_steps_record_the_parameters_they_ran_with(synth_small, tmp_path):
    """Each step's record carries every parameter it ran with: the pipeline's value where it set one (marked changed),
    the default otherwise; a step without parameters records an empty list."""
    maf = {"on": True, "min_on": True, "min": 0.01, "max_on": False, "max": 0.5}
    entries = entries_from_dicts([{"step": "gwashub/parse"}, {"step": "gwashub/filter", "params": {"maf": maf}},
                                  {"step": "gwashub/plots"}, {"step": "gwashub/qc_report"}])
    res = run(synth_small, synth_small["manifest"][0], tmp_path, entries=entries)
    by_step = {s["key"]: s for s in res["steps"]}
    params = {p["key"]: p for p in by_step["gwashub/filter"]["params"]}
    assert set(params) == {f["key"] for f in load_steps()["gwashub/filter"].param_schema}
    assert params["maf"]["changed"] and params["maf"]["value"] == maf and params["maf"]["label"] == "MAF"
    assert not params["beta"]["changed"] and params["beta"]["value"]["on"] is False and params["beta"]["value"]["max"] == 20
    assert by_step["gwashub/plots"]["params"] == []


def test_exit_codes_are_explained():
    """A killed process leaves no error message: the out-of-memory kill is named, with what to change."""
    from pipeline.runner.executor import describe_exit
    for code in (-9, 137):
        assert "ran out of memory" in describe_exit(code) and "step.yaml" in describe_exit(code)
    assert "analysis.yaml" in describe_exit(-9, "method")
    assert describe_exit(1) == "exited with 1" and describe_exit("timeout") == "ran past the time limit"


def test_meta_methods_ask_for_their_job_size():
    """analysis.yaml compute: sizes a method's Batch job, as step.yaml does for QC steps."""
    from pipeline.runner.catalog import load_analyses
    m = load_analyses()
    assert m["gwashub/gwama"].compute["memory_gb"] >= 60 and m["gwashub/metal"].compute["memory_gb"] >= 60
    assert m["custom_example/custom_meta_analysis"].compute == {}


def test_default_pipeline_groups_by_image():
    resolved = resolve(DEFAULT, load_steps(), images=load_images())
    groups = plan_groups(resolved)
    assert [(g["image"], g["steps"]) for g in groups] == [("gwashub/base-r", [0, 1]), ("gwashub/vcf", [2, 3]),
                                                          ("gwashub/ldsc", [4]), ("gwashub/base", [5, 6, 7])]
    # Each Batch job asks for what its most demanding step declares (step.yaml compute:), at least the defaults.
    assert groups[1]["compute"] == {"vcpus": 4, "memory_gb": 60} and groups[3]["compute"]["memory_gb"] >= 8


def test_groups_split_where_the_image_changes():
    entries = entries_from_dicts([{"step": "gwashub/parse"}, {"step": "gwashub/filter"}, {"step": "gwashub/harmonise_liftover"},
                                  {"step": "gwashub/plots"}, {"step": "gwashub/qc_report"}])
    resolved = resolve(entries, load_steps(), images=load_images())
    assert [(g["image"], g["steps"]) for g in plan_groups(resolved)] == [("gwashub/base-r", [0, 1]), ("gwashub/vcf", [2]),
                                                                       ("gwashub/base", [3, 4])]
    # A step runs in its step.yaml image; an image in a saved pipeline is ignored.
    assert resolve(entries_from_dicts([{"step": "gwashub/parse", "image": "nobody/nothing"}]), load_steps(), images=load_images())[0].image == "gwashub/base-r"


def test_images_build_after_their_base():
    images = load_images()
    assert {"gwashub/base", "gwashub/base-r", "gwashub/vcf", "gwashub/ldsc"} <= set(images)
    order = build_order(images)
    assert order.index("gwashub/base") < order.index("gwashub/base-r")
    assert images["gwashub/base"]["platform"] == "linux/arm64" and images["gwashub/vcf"]["platform"] == "linux/amd64"


def test_unknown_step_and_bad_on_fail_rejected():
    with pytest.raises(ValueError):
        resolve([Entry("gwashub/nope")], load_steps())
    with pytest.raises(ValueError):
        resolve([Entry("gwashub/parse", on_fail="explode")], load_steps())


def test_compute_requests_must_fit_one_batch_instance():
    """A step asking for more than the largest instance would wait forever on Batch: the catalog refuses it."""
    import pytest
    from pipeline.runner.catalog import MAX_COMPUTE, _compute
    assert _compute({"vcpus": 4, "memory_gb": 64}, "ok/step.yaml") == {"vcpus": 4, "memory_gb": 64}
    with pytest.raises(ValueError, match="more than one job can have"):
        _compute({"memory_gb": MAX_COMPUTE["memory_gb"] + 1}, "big/step.yaml")


@needs_image(*IMAGES)
def test_a_step_stopped_by_sigterm_interrupts_the_run_without_recording_it(synth_small, tmp_path):
    """SIGTERM (a reclaimed Spot machine) is not a step crash: the run stops at once and records no result, so Batch's
    retry starts the group again instead of finding a "crashed" step."""
    from pipeline.runner.executor import Interrupted
    # Killed by SIGTERM; as process 1 in a container the signal is ignored, so exit as `docker run` reports that (143).
    stop = "import os, signal, sys\nos.kill(os.getpid(), signal.SIGTERM)\nsys.exit(143)"
    extra = _owner_steps(tmp_path / "qc_steps", "testco", {"stopped": ("stopped.py", stop, "")})
    entries = entries_from_dicts([{"step": "gwashub/parse"}, {"step": "testco/stopped"}, {"step": "gwashub/qc_report"}])
    with pytest.raises(Interrupted, match="stopped"):
        run(synth_small, synth_small["manifest"][0], tmp_path / "run", entries=entries, catalog={**load_steps(), **extra})
    assert not (tmp_path / "run" / "result.json").exists()


def test_range_defaults_load_as_written():
    """An unquoted `on:` in step.yaml is YAML's boolean true, which range_value read as switched off. Range defaults
    must arrive as written (every one in the catalog), and a boolean key must be refused."""
    import yaml
    from pipeline.runner.params import range_value
    for key, step in load_steps().items():
        with open(os.path.join(step.workdir, "step.yaml")) as f:
            raw = {p["key"]: p for p in (yaml.safe_load(f).get("params") or []) if isinstance(p, dict)}
        for name, p in raw.items():
            if p.get("type") == "range":
                assert all(isinstance(k, str) for k in p["default"]), f"{key} {name}: quote \"on\""
                assert step.params[name]["on"] == p["default"].get("on", False), (key, name)
    beta = load_steps()["gwashub/filter"].params["beta"]
    assert beta["on"] is False and beta["max_on"] is True and beta["max"] == 20
    with pytest.raises(ValueError, match='"on" in quotes'):
        range_value({True: True, "max_on": True, "max": 20}, ["max"])
