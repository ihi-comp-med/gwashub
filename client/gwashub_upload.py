#!/usr/bin/env python3
# Needs Python 3.9 or later: run it with python3 (python may be Python 2).
"""GWASHub bulk upload: send many summary-statistics files, with their metadata, listed in an Excel manifest.

Uses only the Python standard library (3.9+). You need an API token from the site (Account > API tokens): it works in
one project, can only upload, and lasts 30 days. Give it, and the site's address, as environment variables:

    export GWASHUB_URL=https://gwashub.example.org
    export GWASHUB_TOKEN=gwh_...            # or put it in a file and use --token-file

    python3 gwashub_upload.py template manifest.xlsx   # an empty manifest for the token's project (README inside)
    python3 gwashub_upload.py check  manifest.xlsx     # checks everything, uploads nothing
    python3 gwashub_upload.py upload manifest.xlsx     # checks again, then uploads; run again to resume
    python3 gwashub_upload.py status manifest.xlsx     # what happened to each row

Uploads wait on the site as "Pending QC": QC starts when you (or a reviewer) click Start QC there.
The script keeps its progress in <manifest>.state.json, next to the manifest.
"""
import argparse
import base64
import concurrent.futures
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

_NEEDS = f"This script needs Python 3.9 or later: run it with python3, not python"  # Python 2 stops here and shows this line

SERVER = ""  # filled in when the script is downloaded from the site; GWASHUB_URL or --server override it
PARALLEL_PARTS = 4
RETRIES = 5
COUNTS = ("n_cases", "n_controls", "n_total")


class Fail(Exception):
    """A problem to report and stop on."""


# ---------- talking to the site ----------

class Site:
    def __init__(self, server: str, token: str):
        self.server, self.token = server.rstrip("/"), token

    def url(self, path: str) -> str:
        return path if path.startswith("http") else self.server + path

    def call(self, method: str, path: str, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url(path), data=data, method=method)
        req.add_header("X-GWASHub-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    content = r.read()
                    return content if raw else json.loads(content)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")
                try:
                    detail = json.loads(detail).get("detail", detail)
                except ValueError:
                    pass
                if e.code >= 500 and attempt < RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise SiteError(e.code, detail if isinstance(detail, str) else json.dumps(detail))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise Fail(f"Can't reach {self.server}: {e}")

    def put_part(self, url: str, chunk: bytes) -> str:
        """PUT one part (to S3 or the site) and return its ETag."""
        for attempt in range(RETRIES):
            req = urllib.request.Request(self.url(url), data=chunk, method="PUT")
            req.add_header("Content-Type", "application/octet-stream")
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    return r.headers["ETag"]
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == RETRIES - 1:
                    raise Fail(f"Sending a part failed: {e}")
                time.sleep(2 ** attempt)


class SiteError(Exception):
    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


# ---------- the manifest ----------

def read_manifest(site: "Site", project: str, path: str):
    """(columns, rows) of an .xlsx manifest, read by the site: rows are {row: number in the Manifest sheet, values}."""
    if not path.lower().endswith(".xlsx"):
        raise Fail(f"{path}: give the .xlsx manifest (make one with: python3 gwashub_upload.py template manifest.xlsx)")
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError as e:
        raise Fail(f"Can't read {path}: {e}")
    r = site.call("POST", f"/api/projects/{project}/bulk/parse", {"content": base64.b64encode(content).decode()})
    return r["columns"], [(x["row"], x["values"]) for x in r["rows"]]


def file_path(manifest: str, value: str) -> str:
    return value if os.path.isabs(value) else os.path.join(os.path.dirname(os.path.abspath(manifest)), value)


def read_header(path: str) -> list:
    """The file's column names: first line, tab-, comma- or space-separated, gzipped or not (as the site reads it)."""
    with open(path, "rb") as f:
        magic = f.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        first = f.readline().rstrip("\r\n")
    if not first:
        raise ValueError("the file is empty")
    if "\t" in first:
        return [c.strip() for c in first.split("\t")]
    if first.count(",") >= 2:
        return [c.strip() for c in first.split(",")]
    return first.split()


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_rows(manifest: str, rows, verify_sha=False):
    """The rows as the site's check wants them, reading each file's header (and its sha256 when asked)."""
    out = []
    for n, v in rows:
        r = {"row": n, "values": v, "header": None, "file_error": None, "file_size": None}
        p = file_path(manifest, v.get("file", "")) if v.get("file") else None
        try:
            if not p or not os.path.isfile(p):
                raise ValueError("not found" if p else "no file given")
            r["file_size"] = os.path.getsize(p)
            if not r["file_size"]:
                raise ValueError("the file is empty")
            r["header"] = read_header(p)
            if verify_sha and v.get("sha256"):
                if sha256_of(p) != v["sha256"].lower():
                    raise ValueError("its sha256 doesn't match the manifest's")
        except (OSError, ValueError, EOFError) as e:
            r["file_error"] = str(e)
        out.append(r)
    return out


# ---------- state (for resuming) ----------

def state_path(manifest: str) -> str:
    return manifest + ".state.json"


def load_state(manifest: str) -> dict:
    try:
        with open(state_path(manifest)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"project": None, "files": {}}


def save_state(manifest: str, state: dict):
    tmp = state_path(manifest) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, state_path(manifest))


# ---------- commands ----------

def project_of(site: Site, args) -> str:
    me = site.call("GET", "/api/bulk/whoami")
    if not me.get("token"):
        raise Fail("The site didn't recognise a token")
    print(f"Signed in as {me['name']} ({me['email']}); token '{me['token']['name']}' for project "
          f"{me['token']['project_id']}, expires {me['token']['expires_at'][:10]}", file=sys.stderr)
    return me["token"]["project_id"]


def run_check(site: Site, project: str, manifest: str, own_ids=(), verify_sha=False):
    """The site's check of the whole manifest; returns {row number: upload details} when every row passes, else None."""
    columns, rows = read_manifest(site, project, manifest)
    print(f"Checking {len(rows)} row{'s' if len(rows) != 1 else ''} of {manifest}...", file=sys.stderr)
    r = site.call("POST", f"/api/projects/{project}/bulk/check",
                  {"columns": columns, "rows": check_rows(manifest, rows, verify_sha), "own_ids": list(own_ids)})
    if r["ok"]:
        extra = f"; new cohorts: {', '.join(r['new_cohorts'])}" if r["new_cohorts"] else ""
        print(f"OK: all {r['rows']} rows pass{extra}.")
        return rows, r["uploads"]
    print(f"{len(r['problems'])} problem{'s' if len(r['problems']) != 1 else ''}; nothing was uploaded:")
    for p in r["problems"]:
        print(f"  {'manifest' if p['row'] is None else 'row ' + str(p['row'])}: {p['message']}")
    return None


def cmd_template(site, args):
    project = project_of(site, args)
    if not args.path.lower().endswith(".xlsx"):
        raise Fail("Name the template .xlsx, e.g. manifest.xlsx")
    if os.path.exists(args.path) and not args.force:
        raise Fail(f"{args.path} exists; choose another name (or add --force to replace it)")
    with open(args.path, "wb") as f:
        f.write(site.call("GET", f"/api/projects/{project}/bulk/template", raw=True))
    print(f"Wrote {args.path}: fill in its Manifest sheet (the README sheet explains every column), then run check.")


def cmd_check(site, args):
    project = project_of(site, args)
    state = load_state(args.manifest)
    own = [f["id"] for f in state["files"].values() if f.get("id")] if state.get("project") == project else []
    return 0 if run_check(site, project, args.manifest, own, verify_sha=True) is not None else 1


def cmd_upload(site, args):
    project = project_of(site, args)
    state = load_state(args.manifest)
    if state.get("project") not in (None, project):
        raise Fail(f"{state_path(args.manifest)} belongs to project {state['project']}; move it away to start afresh")
    state["project"] = project
    own = [f["id"] for f in state["files"].values() if f.get("id")]
    result = run_check(site, project, args.manifest, own, verify_sha=True)
    if result is None:
        return 1
    rows, uploads = result
    for i, (n, v) in enumerate(rows, 1):
        key = v["file"]
        entry = state["files"].setdefault(key, {})
        label = f"[{i}/{len(rows)}] {key}"
        if entry.get("done"):
            print(f"{label}: already uploaded (dataset {entry['id']})")
            continue
        upload_one(site, project, args.manifest, v, uploads[str(n)], entry, label, lambda: save_state(args.manifest, state))
    save_state(args.manifest, state)
    print(f"\nDone. The datasets wait as Pending QC: check them on the site, then click Start QC.\n"
          f"  {site.server}/projects/{project}/datasets")
    return 0


def upload_one(site: Site, project: str, manifest: str, v: dict, meta: dict, entry: dict, label: str, save):
    path = file_path(manifest, v["file"])
    size = os.path.getsize(path)
    if entry.get("id"):  # resuming: is that upload still open?
        st = site.call("GET", f"/api/projects/{project}/bulk/status?ids={entry['id']}")[0]["status"]
        if st == "not found":
            print(f"{label}: the earlier upload is gone from the site; starting again")
            entry.clear()
        elif st != "uploading":
            entry["done"] = True
            save()
            print(f"{label}: already uploaded (dataset {entry['id']}, {st})")
            return
    if not entry.get("id"):
        print(f"{label}: computing sha256 of {size / 1e9:.2f} GB..." if size >= 1e8 else f"{label}: computing sha256 of {size / 1e6:.1f} MB...", end="", flush=True)
        sha = v.get("sha256") or sha256_of(path)
        r = site.call("POST", f"/api/projects/{project}/uploads",
                      {"meta": meta, "file_name": os.path.basename(path), "file_size": size, "sha256": sha})
        entry.update(id=r["id"], part_size=r["part_size"], parts=r["parts"], size=size, etags={})
        save()
        print(f" dataset {r['id']}")
    if entry["size"] != size:
        raise Fail(f"{label}: the file changed size since the upload started; delete dataset {entry['id']} on the site "
                   f"and remove it from {state_path(manifest)}")
    todo = [p for p in range(1, entry["parts"] + 1) if str(p) not in entry["etags"]]
    started, sent = time.time(), 0
    for batch in (todo[i:i + 100] for i in range(0, len(todo), 100)):
        urls = site.call("POST", f"/api/datasets/{entry['id']}/upload/urls", {"parts": batch})["urls"]

        def send(p):
            with open(path, "rb") as f:
                f.seek((p - 1) * entry["part_size"])
                chunk = f.read(entry["part_size"])
            return p, len(chunk), site.put_part(urls[str(p)], chunk)

        with concurrent.futures.ThreadPoolExecutor(PARALLEL_PARTS) as pool:
            for p, n_bytes, etag in pool.map(send, batch):
                entry["etags"][str(p)] = etag
                sent += n_bytes
                save()
                rate = sent / max(time.time() - started, 1e-6) / 1e6
                print(f"\r{label}: part {len(entry['etags'])}/{entry['parts']} ({rate:.1f} MB/s)   ", end="", flush=True)
    parts = [{"n": int(p), "etag": e} for p, e in sorted(entry["etags"].items(), key=lambda x: int(x[0]))]
    site.call("POST", f"/api/datasets/{entry['id']}/upload/complete", {"parts": parts})
    entry["done"] = True
    save()
    print(f"\r{label}: uploaded, dataset {entry['id']} (Pending QC)" + " " * 20)


def cmd_status(site, args):
    project = project_of(site, args)
    state = load_state(args.manifest)
    ids = {k: f["id"] for k, f in state["files"].items() if f.get("id")}
    _, rows = read_manifest(site, project, args.manifest)
    found = {s["id"]: s for s in site.call("GET", f"/api/projects/{project}/bulk/status?ids={','.join(map(str, ids.values()))}")} if ids else {}
    for n, v in rows:
        i = ids.get(v["file"])
        s = found.get(i, {}).get("status", "not uploaded") if i else "not uploaded"
        print(f"row {n:<5} {v['file']:<50} {('dataset ' + str(i)) if i else '':<14} {s}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server", default=os.environ.get("GWASHUB_URL") or SERVER, help="the site's address (or GWASHUB_URL)")
    ap.add_argument("--token-file", help="a file holding the API token (otherwise GWASHUB_TOKEN)")
    sub = ap.add_subparsers(dest="command", required=True)
    t = sub.add_parser("template", help="write an empty manifest workbook for the token's project")
    t.add_argument("path", nargs="?", default="manifest.xlsx")
    t.add_argument("--force", action="store_true", help="replace the file if it exists")
    for name, text in (("check", "check a manifest; uploads nothing"), ("upload", "check, then upload (resumes)"),
                       ("status", "show what happened to each row")):
        sub.add_parser(name, help=text).add_argument("manifest")
    args = ap.parse_args(argv)
    token = open(args.token_file).read().strip() if args.token_file else os.environ.get("GWASHUB_TOKEN", "").strip()
    try:
        if not args.server:
            raise Fail("Give the site's address: export GWASHUB_URL=https://... (or --server)")
        if not token.startswith("gwh_"):
            raise Fail("Give your API token (Account > API tokens on the site): export GWASHUB_TOKEN=gwh_... (or --token-file)")
        site = Site(args.server, token)
        return {"template": cmd_template, "check": cmd_check, "upload": cmd_upload, "status": cmd_status}[args.command](site, args) or 0
    except Fail as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except SiteError as e:
        print(f"Error from the site ({e.code}): {e.detail}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to carry on.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
