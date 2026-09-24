import asyncio

import boto3
from botocore.config import Config

from agent_platform.platform.persistence.store import DomainError


class S3Archive:
    def __init__(self, settings):
        self.settings = settings

    async def put(self, key, content):
        s = self.settings
        if (
            not s.knowledge_s3_bucket
            or not s.knowledge_s3_access_key
            or not s.knowledge_s3_secret_key
        ):
            raise DomainError("s3_not_configured", 503)

        def upload():
            client = boto3.client(
                "s3",
                endpoint_url=s.knowledge_s3_endpoint,
                aws_access_key_id=s.knowledge_s3_access_key.get_secret_value(),
                aws_secret_access_key=s.knowledge_s3_secret_key.get_secret_value(),
                region_name="us-east-1",
                config=Config(
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"max_attempts": 1},
                    s3={"addressing_style": "path"},
                ),
            )
            try:
                client.put_object(
                    Bucket=s.knowledge_s3_bucket,
                    Key=key,
                    Body=content,
                    ContentType="application/octet-stream",
                )
            finally:
                client.close()

        await asyncio.to_thread(upload)
        return {"bucket": s.knowledge_s3_bucket, "key": key}
