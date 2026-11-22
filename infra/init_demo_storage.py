"""Wait for local MinIO and ensure the private demo bucket exists."""
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def main():
    region = os.environ.get("AWS_REGION", "us-west-2")
    bucket = os.environ["S3_BUCKET"]
    client = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"], region_name=region,
                          config=Config(signature_version="s3v4", connect_timeout=2, read_timeout=3,
                                        retries={"max_attempts": 0}, s3={"addressing_style": "path"}))
    for attempt in range(60):
        try:
            # Listing our buckets verifies credentials as well as service readiness.
            names = {entry["Name"] for entry in client.list_buckets().get("Buckets", [])}
            if bucket not in names:
                options = {"Bucket": bucket}
                if region != "us-east-1":
                    options["CreateBucketConfiguration"] = {"LocationConstraint": region}
                client.create_bucket(**options)
            print("Private demo object-storage bucket is ready.", flush=True)
            return
        except (BotoCoreError, ClientError):
            if attempt == 59:
                raise SystemExit("Object storage did not become ready; check MinIO status and credentials") from None
            time.sleep(2)


if __name__ == "__main__":
    main()
