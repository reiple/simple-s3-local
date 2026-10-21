"""Usage: python examples/download.py BUCKET KEY OUTPUT [--endpoint URL]"""
import argparse
from pathlib import Path

import boto3
from botocore.config import Config

parser = argparse.ArgumentParser(description="로컬 S3 API에서 파일 다운로드")
parser.add_argument("bucket")
parser.add_argument("key")
parser.add_argument("output")
parser.add_argument("--endpoint", default="http://127.0.0.1:9000")
args = parser.parse_args()

client = boto3.client(
    "s3",
    endpoint_url=args.endpoint,
    aws_access_key_id="local",
    aws_secret_access_key="local",
    region_name="us-east-1",
    config=Config(s3={"addressing_style": "path"}),
)
try:
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=args.bucket):
        for obj in page.get("Contents", []):
            print(obj["Key"], obj["Size"])
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    client.download_file(args.bucket, args.key, args.output)
    print("Downloaded:", args.output)
finally:
    client.close()
