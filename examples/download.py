"""Usage: python examples/download.py BUCKET KEY OUTPUT [--endpoint URL] [--config PATH]"""
import argparse
import configparser
from pathlib import Path

import boto3
from botocore.config import Config

parser = argparse.ArgumentParser(description="로컬 S3 API에서 파일 다운로드")
parser.add_argument("bucket")
parser.add_argument("key")
parser.add_argument("output")
parser.add_argument("--endpoint", default="http://127.0.0.1:9000")
parser.add_argument("--config", default="s3.properties", help="UTF-8 key=value 인증 설정 파일")
args = parser.parse_args()

# The example accepts the simple key=value format shown in s3.properties.example.
credentials = configparser.ConfigParser(interpolation=None)
try:
    credentials.read_string("[credentials]\n" + Path(args.config).read_text(encoding="utf-8"))
    access_key = credentials["credentials"]["accessKey"]
    secret_key = credentials["credentials"]["secretKey"]
    if not access_key.strip() or not secret_key.strip():
        raise ValueError("empty credentials")
except (OSError, configparser.Error, KeyError, ValueError):
    parser.error("설정 파일에서 accessKey와 secretKey를 읽을 수 없습니다")

client = boto3.client(
    "s3",
    endpoint_url=args.endpoint,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    region_name="us-east-1",
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
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
