"""Upload references are independent of the API pod's filesystem in S3 mode."""
import os
from pathlib import Path
from urllib.parse import urlparse

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config


def client(read_timeout=30):
    return boto3.client(
        "s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(connect_timeout=5, read_timeout=read_timeout,
                      retries={"max_attempts": 2}, s3={"addressing_style": "path"}),
    )


def reference(task_id, extension, local_path):
    backend = os.getenv("UPLOAD_STORAGE_BACKEND", "local")
    if backend == "local":
        return str(local_path)
    if backend != "s3":
        raise ValueError("UPLOAD_STORAGE_BACKEND must be local or s3")
    bucket = os.environ["S3_UPLOAD_BUCKET"]
    return f"s3://{bucket}/uploads/{task_id}{extension}"


def object_location(ref):
    parsed = urlparse(ref)
    if parsed.scheme != "s3" or parsed.netloc != os.environ["S3_UPLOAD_BUCKET"]:
        raise ValueError("Upload reference must use the configured bucket")
    key = parsed.path.lstrip("/")
    if not key.startswith("uploads/") or "/" in key[len("uploads/"):] or ".." in key:
        raise ValueError("Invalid upload object key")
    return parsed.netloc, key


def persist(local_path, ref):
    if not ref.startswith("s3://"):
        return
    bucket, key = object_location(ref)
    client().upload_file(str(local_path), bucket, key,
                         Config=TransferConfig(max_concurrency=1, use_threads=False))


def materialize(ref, directory):
    if not ref.startswith("s3://"):
        return ref
    bucket, key = object_location(ref)
    path = Path(directory) / Path(key).name
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        client().download_file(bucket, key, str(path),
                               Config=TransferConfig(max_concurrency=1, use_threads=False))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return str(path)


def delete(ref):
    if ref.startswith("s3://"):
        bucket, key = object_location(ref)
        client().delete_object(Bucket=bucket, Key=key)
    else:
        Path(ref).unlink(missing_ok=True)
