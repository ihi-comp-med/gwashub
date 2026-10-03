"""Run the default pipeline on every fake dataset and print a summary (no API, no AWS).

    python -m devtools.run_demo [--data local/synthetic] [--out local/runs]
"""
import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor

from pipeline.runner.catalog import load_steps
from pipeline.runner.executor import run_pipeline
from pipeline.runner.pipeline import load_template, resolve
from devtools.synth import params_for

def run_one(args):
    d, data, out = args
    resolved = resolve(load_template("pipeline/qc_steps/templates/default.yaml"), load_steps())
    name = d["file"].split(".")[0]
    res = run_pipeline(resolved, os.path.join(data, "fake_gwas", d["file"]), params_for(d),
                       os.path.join(data, "resources"), os.path.join(out, name))
    return name, d, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="local/synthetic")
    ap.add_argument("--out", default="local/runs")
    a = ap.parse_args()
    manifest = json.load(open(os.path.join(a.data, "fake_gwas", "manifest.json")))
    with ProcessPoolExecutor(max_workers=min(6, os.cpu_count() or 2)) as ex:
        results = list(ex.map(run_one, [(d, a.data, a.out) for d in manifest]))
    for name, d, res in results:
        flagged = [f"{s['title']}={s['verdict']}" for s in res["steps"] if s.get("verdict") in ("warn", "fail")]
        print(f"{name:<28} faults={','.join(d['faults']) or '-':<36} {res['status']:<9} {res['overall_verdict']:<5} "
              f"rows={res['outputs']['rows']:>6}  {'; '.join(flagged)}")
        for s in res["steps"]:
            if s.get("verdict") in ("warn", "fail"):
                print(f"{'':30}↳ {s['title']}: {' | '.join(s.get('messages') or [])[:200]}")


if __name__ == "__main__":
    main()
