"""CLI: run a pipeline locally.

    python -m pipeline.runner run --input data/fake_gwas/ukb_hf_eur.tsv.gz --meta meta.json \
        --resources local/synthetic/resources --out local/runs/ukb
"""
import argparse
import json
import sys

from pipeline.runner.catalog import load_steps
from pipeline.runner.executor import run_pipeline
from pipeline.runner.pipeline import load_template, resolve


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pipeline.runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--input", required=True)
    r.add_argument("--params", required=True, help="run params JSON (column_mapping, build, ancestry, ...)")
    r.add_argument("--pipeline", default="pipeline/qc_steps/templates/default.yaml")
    r.add_argument("--resources", default="local/synthetic/resources")
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    with open(a.params) as f:
        params = json.load(f)
    resolved = resolve(load_template(a.pipeline), load_steps())

    def progress(i, state, res):
        if state != "running":
            print(f"  {i + 1:>2}. {resolved[i].step.title:<24} {state:<6} {res.get('duration', '')}s  "
                  f"{(res.get('messages') or [''])[0][:110]}")

    result = run_pipeline(resolved, a.input, params, a.resources, a.out, on_step=progress)
    print(f"status={result['status']} overall={result['overall_verdict']} rows={result['outputs']['rows']}"
          + (f"  ({result['stop_reason']})" if result["stop_reason"] else ""))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
