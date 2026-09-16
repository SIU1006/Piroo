"""Initialize the throwaway S3-compatible development bucket, never AWS S3."""
import os
import time

from botocore.exceptions import BotoCoreError, ClientError

import upload_storage


def main():
    if not os.getenv("S3_ENDPOINT_URL"):
        raise ValueError("Development bucket initialization requires S3_ENDPOINT_URL")
    bucket = os.environ["S3_UPLOAD_BUCKET"]
    for attempt in range(60):
        try:
            s3 = upload_storage.client()
            try:
                s3.head_bucket(Bucket=bucket)
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                    raise
                s3.create_bucket(Bucket=bucket)
            s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={
                "Rules": [{"ID": "expire-abandoned-uploads", "Status": "Enabled",
                           "Filter": {"Prefix": "uploads/"}, "Expiration": {"Days": 1},
                           "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}}],
            })
            return
        except (BotoCoreError, ClientError):
            if attempt == 59:
                raise
            time.sleep(2)


if __name__ == "__main__":
    main()
