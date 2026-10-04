"""End-to-end demo against a running API: upload every fake dataset as a project member, wait for QC,
review the 'review' ones, run meta-analyses, and check the true loci were recovered.

    .venv/bin/uvicorn gwashub_api.main:app --app-dir api --port 8000 &
    python -m devtools.e2e --api http://localhost:8000
"""
import argparse
import json
import os
import sys
import time

import httpx
import pandas as pd

# Which project member uploads each file (uploaders upload; the HERMES reviewers also upload some).
UPLOADERS = {
    "ukb_hf_eur": "jonas@example.org", "finngen_hf_eur": "jonas@example.org", "mvp_hf_afr": "mei@example.org",
    "bbj_hf_eas": "mei@example.org", "estbb_hf_eur_f": "jonas@example.org", "estbb_hf_eur_m": "jonas@example.org", "hunt_hf_eur": "ama@example.org",
    "biovu_hf_eur_neglog10p": "mei@example.org", "partners_hf_eur_swapped": "jonas@example.org",
    "decode_hf_eur_wrongbuild": "demo@gwashub.org", "gnh_hf_sas_nose": "mei@example.org",
    "share_hcm_eur": "noor@example.org", "hcmr_hcm_eur": "demo@gwashub.org", "pk_hcm_sas": "noor@example.org",
}


def session(api, email):
    c = httpx.Client(base_url=api, timeout=120)
    c.post("/api/auth/login", json={"email": email}).raise_for_status()
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--data", default="local/synthetic")
    a = ap.parse_args()
    manifest = json.load(open(os.path.join(a.data, "fake_gwas", "manifest.json")))
    ids = {}
    for d in manifest:
        name = d["file"].split(".")[0]
        project = "hcm" if d["phenotype"] == "HCM" else "hermes3"
        c = session(a.api, UPLOADERS[name])
        path = os.path.join(a.data, "fake_gwas", d["file"])
        with open(path, "rb") as f:  # let the API suggest the mapping, as the upload form does
            sug = c.post(f"/api/projects/{project}/column-suggestion", files={"file": (d["file"], f)}).json()
        mapping = sug["mapping"]
        if "drop_se" in d["faults"]:
            mapping.pop("se", None)
        mismatch = {k: (mapping.get(k), v) for k, v in d["column_mapping"].items() if mapping.get(k) != v}
        meta = {k: d[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "n_cases", "n_controls")}
        meta.update(column_mapping=mapping, effect_type=sug["effect_type"])
        with open(path, "rb") as f:
            r = c.post(f"/api/projects/{project}/datasets", files={"file": (d["file"], f)}, data={"meta": json.dumps(meta)})
        r.raise_for_status()
        ids[name] = (project, r.json()["id"], UPLOADERS[name])
        print(f"uploaded {name:<28} → {project}/{r.json()['id']}  suggested-mapping-diffs={mismatch or 'none'} "
              f"effect={sug['effect_type']} (truth {d['effect_type']})")

    print("waiting for QC…")
    deadline = time.time() + 900
    reviewer = {"hermes3": session(a.api, "ama@example.org"), "hcm": session(a.api, "tom@example.org")}
    while time.time() < deadline:
        pending = [n for n, (p, i, _) in ids.items() if reviewer[p].get(f"/api/datasets/{i}").json()["status"] == "processing"]
        if not pending:
            break
        time.sleep(3)
    else:
        sys.exit(f"timed out waiting for {pending}")

    print(f"\n{'dataset':<28} {'status':<9} {'run':<10} steps")
    for name, (p, i, _) in ids.items():
        d = reviewer[p].get(f"/api/datasets/{i}").json()
        flags = [f"{s['title']}={s['state']}" for s in d["run"]["steps"] if s["state"] in ("warn", "fail", "error")]
        print(f"{name:<28} {d['status']:<9} {d['run']['status']:<10} {'; '.join(flags)}")
        if d["status"] == "review":
            reviewer[p].post(f"/api/datasets/{i}/review", json={"decision": "approve", "note": "Reviewed λGC / dropped rows; acceptable."})

    for project, pheno, name in (("hermes3", "All-cause HF", "All-cause HF, all ancestries"), ("hcm", "HCM", "HCM, all ancestries")):
        c = reviewer[project]
        cands = [x for x in c.get(f"/api/projects/{project}/meta/candidates").json() if x["phenotype"] == pheno and x["status"] == "passed"]
        r = c.post(f"/api/projects/{project}/meta", json={"name": name, "dataset_ids": [x["id"] for x in cands]})
        r.raise_for_status()
        mid = r.json()["id"]
        while c.get(f"/api/meta/{mid}").json()["status"] in ("queued", "running"):
            time.sleep(2)
        ma = c.get(f"/api/meta/{mid}").json()
        if ma["status"] != "completed":
            sys.exit(f"meta-analysis failed: {ma['error']}")
        truth = pd.read_csv(os.path.join(a.data, "truth_causal.tsv"), sep="\t", dtype={"chr": str})
        truth = truth[truth["phenotype"] == pheno]
        found = sum(any(l["chr"] == t.chr and abs(l["pos"] - t.pos38) < 500_000 for l in ma["lead_loci"]) for t in truth.itertuples())
        false_pos = sum(not any(l["chr"] == t.chr and abs(l["pos"] - t.pos38) < 500_000 for t in truth.itertuples()) for l in ma["lead_loci"])
        s = ma["summary"]
        print(f"\nmeta-analysis '{name}': {s['n_datasets']} datasets, {s['n_variants']:,} variants, λGC {s['lambda_gc']}, "
              f"{s['n_loci']} loci → recovered {found}/{len(truth)} true loci, {false_pos} false positives")
    # A second HERMES analysis with project defaults switched to random effects + EUR only.
    c = reviewer["hermes3"]
    eur = [x["id"] for x in c.get("/api/projects/hermes3/meta/candidates").json() if x["ancestry"] == "EUR" and x["status"] == "passed"]
    r = c.post("/api/projects/hermes3/meta", json={"name": "All-cause HF, EUR only (random effects)", "dataset_ids": eur,
                                                     "settings": {"method": "gwashub/ivw", "params": {"model": "random"}}})
    r.raise_for_status()
    print("queued EUR-only random-effects analysis", r.json()["id"])


if __name__ == "__main__":
    main()
