"""File storage: a local folder (development) or S3 (AWS), chosen by GWASHUB_STORAGE.

Files are named by a *location* string kept in the database: an absolute path locally, or
`s3://bucket/key` on AWS. Callers build keys like S3 keys (`uploads/<project>/<dataset>/<file>`) in
one of three areas (uploads, results, resources), which map to three buckets on AWS.

Browsers never stream big files through the API on AWS: uploads go to S3 in parts with presigned
URLs, and downloads are presigned redirects. Locally the same flow is served by the API itself
(see the /api/uploads/local routes), so the website has one code path.
"""
import hashlib
import hmac
import os
import posixpath
import shutil
import time
from urllib.parse import quote

from gwashub_api.config import settings

AREAS = ("uploads", "results", "resources")
# Multipart upload part size. S3 allows 5 MB to 5 GB per part and up to 10,000 parts; local testing
# may set it lower (GWASHUB_PART_SIZE) to exercise several parts with small files.
PART_SIZE = int(os.environ.get("GWASHUB_PART_SIZE", 64 * 1024 * 1024))


def is_s3(loc: str) -> bool:
    return loc.startswith("s3://")


def split_s3(loc: str) -> tuple[str, str]:
    bucket, _, key = loc[len("s3://"):].partition("/")
    return bucket, key


def join(loc: str, *parts: str) -> str:
    """Join a relative path onto a location (works for both local paths and s3:// URIs)."""
    return posixpath.join(loc, *parts) if is_s3(loc) else os.path.join(loc, *parts)


def _s3():
    import boto3
    from botocore.config import Config
    if not hasattr(_s3, "client"):
        _s3.client = boto3.client("s3", region_name=settings().aws_region,
                                  config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    return _s3.client


def location(area: str, key: str) -> str:
    """Where `key` lives in an area: s3://<bucket>/<key> on AWS, or a path inside the local storage dir."""
    if area not in AREAS:
        raise ValueError(f"unknown storage area {area}")
    if not key or key.startswith("/") or any(p in ("", ".", "..") for p in key.split("/")):
        raise ValueError("invalid storage key")
    s = settings()
    if s.storage == "s3":
        return f"s3://{s.buckets[area]}/{key}"
    root = s.storage_dir if area != "results" else os.path.join(s.data_dir, "runs")
    p = os.path.abspath(os.path.join(root, key))
    if not p.startswith(os.path.abspath(root) + os.sep):
        raise ValueError("invalid storage key")
    return p


def save_stream(area: str, key: str, fileobj, chunk=1 << 20) -> tuple[str, int, str]:
    """Write a stream to storage. Returns (location, size, sha256)."""
    loc = location(area, key)
    h, size = hashlib.sha256(), 0

    class Hashing:
        def read(self, n=-1):
            nonlocal size
            b = fileobj.read(chunk if n is None or n < 0 else n)
            h.update(b)
            size += len(b)
            return b

    if is_s3(loc):
        bucket, k = split_s3(loc)
        _s3().upload_fileobj(Hashing(), bucket, k)
    else:
        os.makedirs(os.path.dirname(loc), exist_ok=True)
        with open(loc, "wb") as out:
            src = Hashing()
            while b := src.read(chunk):
                out.write(b)
    return loc, size, h.hexdigest()


def put_file(local_path: str, loc: str):
    if is_s3(loc):
        bucket, k = split_s3(loc)
        _s3().upload_file(local_path, bucket, k)
    elif os.path.abspath(local_path) != os.path.abspath(loc):
        os.makedirs(os.path.dirname(loc), exist_ok=True)
        shutil.copyfile(local_path, loc)


def put_dir(local_dir: str, loc: str):
    """Copy a directory tree to a location (used by jobs to publish their outputs)."""
    for dirpath, _, files in os.walk(local_dir):
        for fn in files:
            full = os.path.join(dirpath, fn)
            put_file(full, join(loc, os.path.relpath(full, local_dir).replace(os.sep, "/")))


def get_file(loc: str, dest: str) -> str:
    """Make a local copy of a stored file (S3 download, or a symlink for local storage)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if is_s3(loc):
        bucket, k = split_s3(loc)
        _s3().download_file(bucket, k, dest)
    elif not os.path.exists(dest):
        os.symlink(loc, dest)
    return dest


def read_bytes(loc: str) -> bytes:
    if is_s3(loc):
        bucket, k = split_s3(loc)
        return _s3().get_object(Bucket=bucket, Key=k)["Body"].read()
    with open(loc, "rb") as f:
        return f.read()


def exists(loc: str) -> bool:
    if is_s3(loc):
        from botocore.exceptions import ClientError
        bucket, k = split_s3(loc)
        try:
            _s3().head_object(Bucket=bucket, Key=k)
            return True
        except ClientError:
            return False
    return os.path.isfile(loc)


def size(loc: str) -> int | None:
    if is_s3(loc):
        from botocore.exceptions import ClientError
        bucket, k = split_s3(loc)
        try:
            return _s3().head_object(Bucket=bucket, Key=k)["ContentLength"]
        except ClientError:
            return None
    return os.path.getsize(loc) if os.path.isfile(loc) else None


def list_files(loc: str) -> list[dict]:
    """Every file under a folder location: [{"path": relative path, "size": bytes}], sorted by path."""
    out = []
    if is_s3(loc):
        bucket, prefix = split_s3(loc)
        prefix = prefix.rstrip("/") + "/"
        for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            out += [{"path": o["Key"][len(prefix):], "size": o["Size"]} for o in page.get("Contents", [])]
    elif os.path.isdir(loc):
        for dirpath, _, files in os.walk(loc):
            for fn in files:
                full = os.path.join(dirpath, fn)
                out.append({"path": os.path.relpath(full, loc).replace(os.sep, "/"), "size": os.path.getsize(full)})
    return sorted(out, key=lambda f: f["path"])


def delete_prefix(loc: str):
    """Delete everything under a folder location. On AWS the buckets are versioned, so deleted
    objects stay recoverable until the lifecycle rule expires old versions."""
    if not loc:
        return
    if is_s3(loc):
        bucket, prefix = split_s3(loc)
        prefix = prefix.rstrip("/") + "/"
        pages = _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
        for page in pages:
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                _s3().delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
    else:
        data_dir = os.path.abspath(settings().data_dir) + os.sep
        if os.path.abspath(loc).startswith(data_dir):
            shutil.rmtree(loc, ignore_errors=True)


def download_url(loc: str, filename: str | None = None, inline: bool = False, content_type: str | None = None) -> str | None:
    """A short-lived presigned URL for an S3 object; None for local files (the API serves those itself)."""
    if not is_s3(loc):
        return None
    bucket, k = split_s3(loc)
    params = {"Bucket": bucket, "Key": k}
    if filename:
        params["ResponseContentDisposition"] = f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename)}"
    if content_type:
        params["ResponseContentType"] = content_type
    return _s3().generate_presigned_url("get_object", Params=params, ExpiresIn=settings().presign_seconds)


# ---------- multipart uploads (browser → storage in parts) ----------

def start_multipart(loc: str) -> str:
    if is_s3(loc):
        bucket, k = split_s3(loc)
        return _s3().create_multipart_upload(Bucket=bucket, Key=k)["UploadId"]
    upload_id = hashlib.sha256(f"{loc}{time.time_ns()}".encode()).hexdigest()[:24]
    os.makedirs(_parts_dir(loc, upload_id), exist_ok=True)
    return upload_id


def _parts_dir(loc: str, upload_id: str) -> str:
    return os.path.join(settings().data_dir, "tmp", "multipart", upload_id)


def local_part_token(dataset_id: int, upload_id: str, part: int, expires: int) -> str:
    msg = f"{dataset_id}:{upload_id}:{part}:{expires}".encode()
    return hmac.new(settings().secret.encode(), msg, hashlib.sha256).hexdigest()


def part_url(loc: str, upload_id: str, part: int, dataset_id: int) -> str:
    """Where the browser PUTs one part. S3: a presigned UploadPart URL. Local: a signed API URL."""
    if is_s3(loc):
        bucket, k = split_s3(loc)
        return _s3().generate_presigned_url("upload_part", Params={"Bucket": bucket, "Key": k, "UploadId": upload_id,
                                                                   "PartNumber": part}, ExpiresIn=settings().presign_seconds)
    expires = int(time.time()) + settings().presign_seconds
    token = local_part_token(dataset_id, upload_id, part, expires)
    return f"/api/uploads/local/{dataset_id}/{upload_id}/{part}?expires={expires}&token={token}"


def save_local_part(loc: str, upload_id: str, part: int, fileobj) -> str:
    p = os.path.join(_parts_dir(loc, upload_id), f"{part:05d}")
    h = hashlib.md5()
    with open(p, "wb") as out:
        while b := fileobj.read(1 << 20):
            h.update(b)
            out.write(b)
    return f'"{h.hexdigest()}"'


def complete_multipart(loc: str, upload_id: str, parts: list[dict]) -> int:
    """parts: [{"n": 1, "etag": "..."}]. Returns the file size."""
    parts = sorted(parts, key=lambda p: p["n"])
    if is_s3(loc):
        bucket, k = split_s3(loc)
        _s3().complete_multipart_upload(Bucket=bucket, Key=k, UploadId=upload_id,
                                        MultipartUpload={"Parts": [{"PartNumber": p["n"], "ETag": p["etag"]} for p in parts]})
        return _s3().head_object(Bucket=bucket, Key=k)["ContentLength"]
    d = _parts_dir(loc, upload_id)
    os.makedirs(os.path.dirname(loc), exist_ok=True)
    with open(loc, "wb") as out:
        for p in parts:
            with open(os.path.join(d, f"{p['n']:05d}"), "rb") as f:
                shutil.copyfileobj(f, out)
    shutil.rmtree(d, ignore_errors=True)
    return os.path.getsize(loc)


def abort_multipart(loc: str, upload_id: str):
    if is_s3(loc):
        from botocore.exceptions import ClientError
        bucket, k = split_s3(loc)
        try:
            _s3().abort_multipart_upload(Bucket=bucket, Key=k, UploadId=upload_id)
        except ClientError:
            pass
    else:
        shutil.rmtree(_parts_dir(loc, upload_id), ignore_errors=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
