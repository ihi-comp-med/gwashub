import os

import boto3


def _client(client=None):
    return client or boto3.client("s3", region_name="us-east-1")


def parse_s3_uri(uri):
    if not isinstance(uri, str) or not uri.startswith("s3://"):
        raise ValueError(f"not an s3 uri: {uri!r}")
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ValueError(f"s3 uri missing bucket or key: {uri!r}")
    return bucket, key


def download_object(bucket, key, dest_path, client=None):
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _client(client).download_file(bucket, key, dest_path)


def upload_file(local_path, bucket, key, client=None):
    _client(client).upload_file(local_path, bucket, key)
