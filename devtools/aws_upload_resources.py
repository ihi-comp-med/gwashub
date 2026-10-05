"""Copy the built reference files (local/resources/) to an environment's resources bucket, then register them.

    python devtools/aws_upload_resources.py dev                      # every file in local/resources/BUILT.tsv
    python devtools/aws_upload_resources.py dev genes_b38.tsv.gz     # just these
    ./devtools/aws_admin.sh dev add-resources s3://.../manifest.json # printed at the end: registers them

The website's upload dialog isn't practical for tens of GB, so this copies straight to S3 from this machine
(AWS_PROFILE gwashub-<env>). Each file goes to resources/shared/<key>/<sha256 prefix>/<key>, so a file already
there is skipped and a rebuilt file gets its own path. Titles, builds and sources come from resources/catalog.yaml.
The manifest stays in the bucket: after a database reset, re-run the printed add-resources command; nothing is
uploaded again.
"""
import csv
import datetime
import json
import os
import sys

import boto3
import yaml
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILT_DIR = os.path.join(ROOT, "local", "resources")


def resources_bucket(env: str) -> str:
    with open(os.path.join(ROOT, "infra", f"cdk-outputs-{env}.json")) as f:
        data = json.load(f)[f"Gwashub-{env}-Data"]
    return next(v for k, v in data.items() if k.startswith("ExportsOutputRefResources"))


def manifest_entries(keys: list[str] | None) -> list[dict]:
    """One manifest entry per built file (BUILT.tsv: sha256 and size), described by its catalog.yaml entry."""
    with open(os.path.join(ROOT, "resources", "catalog.yaml")) as f:
        catalog = {k: e for e in yaml.safe_load(f)["resources"] for k in e["keys"]}
    with open(os.path.join(BUILT_DIR, "BUILT.tsv")) as f:
        built = {r["key"]: r for r in csv.DictReader(f, delimiter="\t")}
    unknown = [k for k in (keys or []) if k not in built]
    if unknown:
        sys.exit(f"Not in local/resources/BUILT.tsv (build them first): {', '.join(unknown)}")
    entries = []
    for key in keys or list(built):
        if key not in catalog:
            sys.exit(f"{key} isn't in resources/catalog.yaml")
        c, b = catalog[key], built[key]
        path = os.path.join(BUILT_DIR, key)
        if os.path.getsize(path) != int(b["bytes"]):
            sys.exit(f"{path} has changed since BUILT.tsv was written; rebuild it")
        licence = f" Licence: {c['licence']}." if c.get("licence") else ""
        entries.append({"key": key, "title": c["title"], "description": f"{c['version']}.{licence}", "build": c["build"],
                        "source": c["source"], "sha256": b["sha256"], "size": int(b["bytes"]), "path": path})
    return entries


def main(argv: list[str]):
    if not argv:
        sys.exit(__doc__)
    env, keys = argv[0], argv[1:] or None
    entries = manifest_entries(keys)
    bucket = resources_bucket(env)
    s3 = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", f"gwashub-{env}"),
                       region_name=os.environ.get("AWS_REGION", "eu-west-2")).client("s3")
    transfer = TransferConfig(multipart_chunksize=64 * 1024 * 1024, max_concurrency=8)
    total = sum(e["size"] for e in entries)
    print(f"{len(entries)} files, {total / 1e9:.1f} GB, to s3://{bucket}")
    for e in entries:
        k = f"resources/shared/{e['key']}/{e['sha256'][:16]}/{e['key']}"
        e["storage_path"] = f"s3://{bucket}/{k}"
        try:
            if s3.head_object(Bucket=bucket, Key=k)["ContentLength"] == e["size"]:
                print(f"  {e['key']:45} already there")
                continue
        except ClientError as err:
            if err.response["Error"]["Code"] not in ("404", "NoSuchKey"):
                raise
        print(f"  {e['key']:45} {e['size'] / 1e9:6.2f} GB ...", flush=True)
        s3.upload_file(e.pop("path"), bucket, k, Config=transfer)
    for e in entries:
        e.pop("path", None)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mk = f"resources/shared/_manifests/{stamp}.json"
    s3.put_object(Bucket=bucket, Key=mk, Body=json.dumps({"resources": entries}, indent=1).encode(),
                  ContentType="application/json")
    print(f"Uploaded. Register them (after the new image is deployed):\n"
          f"  ./devtools/aws_admin.sh {env} add-resources s3://{bucket}/{mk}")


if __name__ == "__main__":
    main(sys.argv[1:])
