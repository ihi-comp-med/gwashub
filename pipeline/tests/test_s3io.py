import boto3
import pytest
from moto import mock_aws

from pipeline.runner.aws.s3io import parse_s3_uri, download_object, upload_file


def test_parse_s3_uri_splits_bucket_and_key():
    assert parse_s3_uri("s3://my-bucket/a/b/c.tsv") == ("my-bucket", "a/b/c.tsv")


def test_parse_s3_uri_rejects_non_s3():
    with pytest.raises(ValueError):
        parse_s3_uri("/local/path.tsv")


@mock_aws
def test_download_object_writes_file(tmp_path):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-bucket")
    client.put_object(Bucket="test-bucket", Key="in/data.tsv", Body=b"CHR\tP\n1\t0.5\n")
    dest = tmp_path / "nested" / "data.tsv"
    download_object("test-bucket", "in/data.tsv", str(dest), client=client)
    assert dest.read_text() == "CHR\tP\n1\t0.5\n"


@mock_aws
def test_upload_file_puts_object(tmp_path):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-bucket")
    src = tmp_path / "out.html"
    src.write_text("<html></html>")
    upload_file(str(src), "test-bucket", "out/report.html", client=client)
    body = client.get_object(Bucket="test-bucket", Key="out/report.html")["Body"].read()
    assert body == b"<html></html>"
