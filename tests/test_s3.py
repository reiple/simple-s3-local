import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError


class S3IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        bucket = root / "sample"
        bucket.mkdir()
        (root / "empty-bucket").mkdir()
        cls.files = {
            "hello.txt": b"hello local S3\n",
            "empty.bin": b"",
            "dir/a.txt": b"a",
            "dir/b.txt": b"b",
            "dir/deep/c.txt": b"c",
            "한글 + & <test>.txt": "로컬 파일\n".encode(),
            "big.bin": bytes(range(256)) * (9 * 4096 + 7),
        }
        for key, data in cls.files.items():
            path = bucket / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (root / "secret.txt").write_text("secret")
        (bucket / "leak.txt").symlink_to(root / "secret.txt")
        (bucket / "linked-dir").symlink_to(root, target_is_directory=True)
        (root / "linked-bucket").symlink_to(bucket, target_is_directory=True)
        (bucket / "back\\slash.txt").write_bytes(b"unsupported key")
        private = root / "private"
        private.mkdir()
        (private / "denied.txt").write_bytes(b"private")
        (private / "denied.txt").chmod(0)
        (root / "unreadable").mkdir(mode=0)
        image = os.environ.get("S3_TEST_IMAGE", "local-s3-java8:test")
        cls.container = subprocess.check_output([
            "docker", "run", "--rm", "-d", "--user", str(os.getuid()) + ":" + str(os.getgid()),
            "-p", "127.0.0.1::9000",
            "--mount", "type=bind,src=" + str(root) + ",dst=/data,readonly", image,
        ], text=True).strip()
        cls.addClassCleanup(subprocess.run, ["docker", "rm", "-f", cls.container],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = subprocess.check_output(["docker", "port", cls.container, "9000/tcp"], text=True).strip().rsplit(":", 1)[1]
        cls.endpoint = "http://127.0.0.1:" + port
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.endpoint, timeout=1).close()
                break
            except urllib.error.HTTPError as error:
                error.close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Server did not start")
        cls.client = boto3.client(
            "s3", endpoint_url=cls.endpoint, region_name="us-east-1",
            aws_access_key_id="test", aws_secret_access_key="test",
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 0}),
        )
        cls.addClassCleanup(cls.client.close)

    def test_list_objects_and_metadata(self):
        result = self.client.list_objects_v2(Bucket="sample")
        self.assertFalse(result["IsTruncated"])
        self.assertEqual(result["KeyCount"], len(self.files))
        self.assertEqual([o["Key"] for o in result["Contents"]],
                         sorted(self.files, key=lambda s: s.encode()))
        self.assertEqual({o["Key"]: o["Size"] for o in result["Contents"]},
                         {k: len(v) for k, v in self.files.items()})

    def test_prefix_delimiter_and_pagination(self):
        pages = list(self.client.get_paginator("list_objects_v2").paginate(
            Bucket="sample", Prefix="dir/", Delimiter="/", PaginationConfig={"PageSize": 1}))
        entries = [o["Key"] for p in pages for o in p.get("Contents", [])]
        prefixes = [o["Prefix"] for p in pages for o in p.get("CommonPrefixes", [])]
        self.assertEqual(entries, ["dir/a.txt", "dir/b.txt"])
        self.assertEqual(prefixes, ["dir/deep/"])
        self.assertTrue(all(p["KeyCount"] == 1 for p in pages))
        pages = list(self.client.get_paginator("list_objects_v2").paginate(
            Bucket="sample", PaginationConfig={"PageSize": 2}))
        self.assertEqual([o["Key"] for p in pages for o in p.get("Contents", [])],
                         sorted(self.files, key=lambda s: s.encode()))
        result = self.client.list_objects_v2(Bucket="sample", Prefix="dir/", StartAfter="dir/a.txt")
        self.assertEqual([o["Key"] for o in result["Contents"]], ["dir/b.txt", "dir/deep/c.txt"])

    def test_download_small_unicode_empty_and_parallel(self):
        with tempfile.TemporaryDirectory() as output:
            for i, (key, expected) in enumerate(self.files.items()):
                dest = Path(output) / str(i)
                self.client.download_file("sample", key, str(dest), Config=TransferConfig(
                    multipart_threshold=1024 * 1024, multipart_chunksize=1024 * 1024,
                    max_concurrency=3))
                self.assertEqual(hashlib.sha256(dest.read_bytes()).digest(),
                                 hashlib.sha256(expected).digest(), key)

    def test_get_head_and_ranges(self):
        expected = self.files["hello.txt"]
        head = self.client.head_object(Bucket="sample", Key="hello.txt")
        self.assertEqual(head["ContentLength"], len(expected))
        self.assertIn("ETag", head)
        for spec, data in [("bytes=0-4", expected[:5]), ("bytes=6-", expected[6:]),
                           ("bytes=-4", expected[-4:]), ("bytes=0-999", expected)]:
            with self.subTest(spec=spec):
                result = self.client.get_object(Bucket="sample", Key="hello.txt", Range=spec)
                self.assertEqual(result["ResponseMetadata"]["HTTPStatusCode"], 206)
                with result["Body"] as body:
                    self.assertEqual(body.read(), data)
                self.assertEqual(result["ContentLength"], len(data))
        result = self.client.get_object(Bucket="sample", Key="hello.txt")
        with result["Body"] as body:
            self.assertEqual(body.read(), expected)
        with self.assertRaises(ClientError) as error:
            self.client.get_object(Bucket="sample", Key="hello.txt", IfMatch='"wrong"')
        self.assertEqual(error.exception.response["ResponseMetadata"]["HTTPStatusCode"], 412)

    def test_empty_listing(self):
        for kwargs in [{"Bucket": "empty-bucket"}, {"Bucket": "sample", "Prefix": "missing/"},
                       {"Bucket": "sample", "MaxKeys": 0}]:
            result = self.client.list_objects_v2(**kwargs)
            self.assertEqual(result["KeyCount"], 0)
            self.assertFalse(result["IsTruncated"])

    @unittest.skipIf(os.getuid() == 0, "Permission checks require a non-root test user")
    def test_io_errors_return_s3_responses(self):
        for method, kwargs in [
            ("get_object", {"Bucket": "private", "Key": "denied.txt"}),
            ("list_objects_v2", {"Bucket": "unreadable"}),
        ]:
            with self.subTest(method=method):
                with self.assertRaises(ClientError) as error:
                    getattr(self.client, method)(**kwargs)
                self.assertEqual(error.exception.response["ResponseMetadata"]["HTTPStatusCode"], 403)
                self.assertEqual(error.exception.response["Error"]["Code"], "AccessDenied")

    def test_errors_and_path_safety(self):
        cases = [
            ("get_object", {"Bucket": "sample", "Key": "missing"}, 404),
            ("get_object", {"Bucket": "sample", "Key": "leak.txt"}, 403),
            ("get_object", {"Bucket": "sample", "Key": "linked-dir/secret.txt"}, 403),
            ("get_object", {"Bucket": "sample", "Key": "../secret.txt"}, 403),
            ("get_object", {"Bucket": "sample", "Key": "hello.txt", "Range": "bytes=999-"}, 416),
            ("get_object", {"Bucket": "sample", "Key": "empty.bin", "Range": "bytes=0-"}, 416),
            ("list_objects_v2", {"Bucket": "missing"}, 404),
            ("list_objects_v2", {"Bucket": "linked-bucket"}, 403),
            ("list_objects_v2", {"Bucket": "sample", "MaxKeys": -1}, 400),
            ("list_objects_v2", {"Bucket": "sample", "ContinuationToken": "!invalid"}, 400),
        ]
        for method, kwargs, status in cases:
            with self.subTest(method=method, kwargs=kwargs):
                with self.assertRaises(ClientError) as error:
                    getattr(self.client, method)(**kwargs)
                self.assertEqual(error.exception.response["ResponseMetadata"]["HTTPStatusCode"], status)
        request = urllib.request.Request(self.endpoint + "/sample/new", data=b"no", method="PUT")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 405)
        error.exception.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
