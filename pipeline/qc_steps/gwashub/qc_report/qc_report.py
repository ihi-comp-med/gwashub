"""QC report: roll up every step's verdict, metrics and plots into one self-contained HTML file.

Originally qc-pipeline's qc_report.py. It reads the other steps' logs from GWASHUB_STEP_LOGS
(JSON lines) and their artifacts from GWASHUB_RUN_DIR; the legacy --step-logs / --output-dir
flags still work. It also shows the rows each step removed (from the runner's counts) and every
numeric column before QC (the runner's profile of the upload, GWASHUB_RUN_DIR/profile_input.json) and
after (its own --input, which is the cleaned file by the time the report runs).
"""
import argparse
import base64
import html
import json
import os

ORDER = {"pass": 0, "warn": 1, "fail": 2}
COLORS = {"pass": "#15803d", "warn": "#b45309", "fail": "#b91c1c"}
STATS = ("n", "missing", "mean", "median", "sd", "min", "max")
STAT_LABEL = {"n": "Values", "missing": "Missing", "mean": "Mean", "median": "Median", "sd": "SD", "min": "Min", "max": "Max"}


def fmt(v):
    if v is None:
        return "–"
    if isinstance(v, int):
        return f"{v:,}"
    return f"{v:.3g}" if abs(v) < 1e-3 and v != 0 else f"{v:,.4g}"


def rows_table(logs):
    """Rows in and out of each step, from the runner's counts (absent when a step log came from elsewhere)."""
    out = []
    for log in logs:
        before, after = (log.get("_rows") or [None, None])[:2]
        if after is None:
            continue
        removed = before - after if before is not None else None
        out.append(f"<tr><td>{html.escape(str(log.get('step', '')))}</td><td class='n'>{fmt(before)}</td><td class='n'>{fmt(after)}</td>"
                   f"<td class='n'>{fmt(removed) if removed else '–'}</td></tr>")
    if not out:
        return ""
    return ("<h2>Rows by step</h2><table class='t'><tr><th>Step</th><th>Rows in</th><th>Rows out</th><th>Removed</th></tr>"
            + "".join(out) + "</table>")


def profile_table(before, after):
    """Each numeric column as uploaded and after QC."""
    if not before and not after:
        return ""
    cols = [c for c in ("beta", "se", "p", "eaf", "n", "info") if c in ((before or {}).get("columns") or {}) or c in ((after or {}).get("columns") or {})]
    head = "".join(f"<th>{STAT_LABEL[s]}</th>" for s in STATS)
    body = []
    for c in cols:
        for label, prof in (("uploaded", before), ("after QC", after)):
            s = ((prof or {}).get("columns") or {}).get(c)
            cells = "".join(f"<td class='n'>{fmt(s.get(k))}</td>" for k in STATS) if s else f"<td colspan='{len(STATS)}' class='muted'>not in file</td>"
            body.append(f"<tr><td>{c.upper() if label == 'uploaded' else ''}</td><td class='muted'>{label}</td>{cells}</tr>")
    rows = f"<p>Rows: {fmt((before or {}).get('rows'))} uploaded, {fmt((after or {}).get('rows'))} after QC.</p>"
    note = "<p class='muted'>Uploaded values are as received (odds ratios before conversion to BETA).</p>"
    return f"<h2>Columns before and after QC</h2>{rows}<table class='t'><tr><th>Column</th><th></th>{head}</tr>{''.join(body)}</table>{note}"


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


def build_report(logs, run_dir, params, profiles=(None, None)):
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
ul{{margin:0 0 6px;padding-left:18px}}.t td,.t th{{padding:4px 8px;font-size:13px;text-align:left}}.t .n{{text-align:right;font-variant-numeric:tabular-nums}}
.muted{{color:#5b6b77}}figure{{display:inline-block;margin:12px}}img{{max-width:100%;border:1px solid #e3e8ec}}</style></head>
<body><h1>QC report</h1><p>{study}</p>
<p>Overall verdict: <span class='v' style='background:{COLORS[overall]}'>{overall}</span></p>
<table>{''.join(rows)}</table>{rows_table(logs)}{profile_table(*profiles)}<h2>Plots</h2>{''.join(images) or '<p>No plots.</p>'}</body></html>"""


def read_profiles(working):
    before = after = None
    run_dir = os.environ.get("GWASHUB_RUN_DIR")
    if run_dir and os.path.exists(os.path.join(run_dir, "profile_input.json")):
        with open(os.path.join(run_dir, "profile_input.json")) as f:
            before = json.load(f)
    try:
        from _lib.profile import profile_working
        after = profile_working(working)
        if not after["columns"]:  # the working file was never harmonised (an early step failed)
            after = None
    except Exception:
        pass
    return before, after


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
        f.write(build_report(logs, os.environ.get("GWASHUB_RUN_DIR"), params, read_profiles(args.input)))

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
