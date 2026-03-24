from bifrost import workflow
import logging
import os
import time
import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

@workflow(category="Smoke")
async def minio_put_list():
    """Put and list an object in configured S3 (MinIO) to verify connectivity.

    Expects environment variables:
      BIFROST_S3_ENDPOINT, BIFROST_S3_ACCESS_KEY, BIFROST_S3_SECRET_KEY, BIFROST_S3_BUCKET
    Returns a dict with status and details.
    """
    endpoint = os.environ.get("BIFROST_S3_ENDPOINT")
    access = os.environ.get("BIFROST_S3_ACCESS_KEY")
    secret = os.environ.get("BIFROST_S3_SECRET_KEY")
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not all([endpoint, access, secret, bucket]):
        msg = "Missing S3 config in environment"
        logger.error(msg)
        return {"ok": False, "error": msg}
    try:
        # boto3 client using S3-compatible endpoint (MinIO)
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            config=boto3.session.Config(signature_version='s3v4')
        )
        key = f"bifrost-smoke-{int(time.time())}.txt"
        s3.put_object(Bucket=bucket, Key=key, Body=b"bifrost-smoke")
        objs = s3.list_objects_v2(Bucket=bucket, Prefix="bifrost-smoke-")
        count = objs.get("KeyCount", 0)
        return {"ok": True, "bucket": bucket, "key": key, "found": count}
    except Exception as e:
        logger.exception("MinIO smoke test failed")
        return {"ok": False, "error": str(e)}
