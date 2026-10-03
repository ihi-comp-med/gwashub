"""QC report: roll up every step's verdict, metrics and plots into one self-contained HTML file.

Originally qc-pipeline's qc_report.py. It reads the other steps' logs from GWASHUB_STEP_LOGS
(JSON lines) and their artifacts from GWASHUB_RUN_DIR; the legacy --step-logs / --output-dir
flags still work.
"""
import argparse
import base64
import html
import json
import os

ORDER = {"pass": 0, "warn": 1, "fail": 2}
COLORS = {"pass": "#15803d", "warn": "#b45309", "fail": "#b91c1c"}


def aggregate_verdict(logs):
    worst = "pass"
    for log in logs:
        v = log.get("verdict", "pass")
        if ORDER.get(v, 0) > ORDER[worst]:
            worst = v
    return worst


def embed(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def build_report(logs, run_dir, params):
    overall = aggregate_verdict(logs)
    rows, images = [], []
    for log in logs:
        metrics = "".join(f"<span class='m'><b>{html.escape(str(k))}</b> {html.escape(f'{v:,}' if isinstance(v, int) else str(v))}</span>"
                          for k, v in (log.get("metrics") or {}).items())
        msgs = "".join(f"<li>{html.escape(m)}</li>" for m in log.get("messages") or [])
        v = log.get("verdict", "")
        rows.append(f"<tr><td>{html.escape(str(log.get('step', '')))}</td>"
                    f"<td><span class='v' style='background:{COLORS.get(v, '#666')}'>{html.escape(v)}</span></td>"
                    f"<td><ul>{msgs}</ul>{metrics}</td></tr>")
        for a in log.get("artifacts") or []:
            p = os.path.join(run_dir or "", log.get("_dir", ""), a.get("path", ""))
            if a.get("type") == "image" and os.path.exists(p):
                images.append(f"<figure><img src='{embed(p)}' alt='{html.escape(a.get('label', ''))}'><figcaption>{html.escape(a.get('label', ''))}</figcaption></figure>")
    study = " · ".join(html.escape(str(params.get(k))) for k in ("cohort", "phenotype", "ancestry", "sex") if params.get(k))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>GWASHub QC report</title>
<style>body{{font-family:system-ui,sans-serif;margin:32px;color:#1d2a33;max-width:1100px}}
table{{border-collapse:collapse;width:100%}}td{{border-top:1px solid #e3e8ec;padding:8px;vertical-align:top}}
.v{{color:#fff;border-radius:10px;padding:2px 10px;font-size:13px}}.m{{display:inline-block;margin-right:14px;font-size:13px;color:#5b6b77}}
ul{{margin:0 0 6px;padding-left:18px}}figure{{display:inline-block;margin:12px}}img{{max-width:100%;border:1px solid #e3e8ec}}</style></head>
<body><h1>QC report</h1><p>{study}</p>
<p>Overall verdict: <span class='v' style='background:{COLORS[overall]}'>{overall}</span></p>
<table>{''.join(rows)}</table><h2>Plots</h2>{''.join(images) or '<p>No plots.</p>'}</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--resources", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--step-logs", default=os.environ.get("GWASHUB_STEP_LOGS"))
    ap.add_argument("--output-dir", default=os.environ.get("GWASHUB_ARTIFACT_DIR"))
    args = ap.parse_args()

    logs = []
    if args.step_logs and os.path.exists(args.step_logs):
        with open(args.step_logs) as f:
            logs = [json.loads(line) for line in f if line.strip()]
    with open(args.params) as f:
        params = json.load(f)

    out_dir = args.output_dir or os.path.dirname(args.log)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "qc_report.html"), "w") as f:
        f.write(build_report(logs, os.environ.get("GWASHUB_RUN_DIR"), params))

    with open(args.log, "w") as f:
        json.dump({
            "step": "qc_report",
            "verdict": "pass",
            "metrics": {"steps_reported": len(logs)},
            "messages": [f"Report covers {len(logs)} steps; overall {aggregate_verdict(logs)}."],
            "artifacts": [{"type": "report", "path": "qc_report.html", "label": "QC report"}],
        }, f)


if __name__ == "__main__":
    main()
