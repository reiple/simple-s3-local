import hashlib
from contextlib import closing
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
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
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
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
            "+plus.txt": b"plus",
            " space.txt": b"space",
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
        cls.config_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.config_dir.cleanup)
        cls.config_path = Path(cls.config_dir.name) / "s3.properties"
        cls.config_path.write_text("accessKey=test\nsecretKey=test-secret-한글\n", encoding="utf-8")
        image = os.environ.get("S3_TEST_IMAGE", "local-s3-java8:test")
        cls.container = subprocess.check_output([
            "docker", "run", "--rm", "-d", "--user", str(os.getuid()) + ":" + str(os.getgid()),
            "-p", "127.0.0.1::9000",
            "--mount", "type=bind,src=" + str(root) + ",dst=/data,readonly",
            "--mount", "type=bind,src=" + str(cls.config_path) + ",dst=/app/s3.properties,readonly", image,
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
            aws_access_key_id="test", aws_secret_access_key="test-secret-한글",
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 0}),
        )
        cls.addClassCleanup(cls.client.close)

    def signed_request(self, path, method="GET", headers=None, timestamp=None):
        request = AWSRequest(method=method, url=self.endpoint + path, headers=headers)
        signer = S3SigV4Auth(Credentials("test", "test-secret-한글"), "s3", "us-east-1")
        if timestamp is None:
            signer.add_auth(request)
        else:
            request.context["timestamp"] = timestamp
            signer._modify_request_before_signing(request)
            canonical = signer.canonical_request(request)
            signature = signer.signature(signer.string_to_sign(request, canonical), request)
            signer._inject_signature_to_request(request, signature)
        return urllib.request.Request(request.url, headers=dict(request.headers), method=method)

    def assert_http_error(self, request, code, status=403):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        with error.exception as response:
            self.assertEqual(response.code, status)
            if request.get_method() != "HEAD":
                self.assertEqual(ET.fromstring(response.read()).findtext("Code"), code)

    def test_wrong_access_key_and_secret_rejected(self):
        for access, secret, code in [
            ("wrong", "test-secret-한글", "InvalidAccessKeyId"),
            ("test", "wrong", "SignatureDoesNotMatch"),
        ]:
            with closing(boto3.client(
                "s3", endpoint_url=self.endpoint, region_name="us-east-1",
                aws_access_key_id=access, aws_secret_access_key=secret,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                              retries={"max_attempts": 0}),
            )) as client:
                for operation, kwargs in [
                    ("list_objects_v2", {"Bucket": "sample"}),
                    ("get_object", {"Bucket": "sample", "Key": "hello.txt"}),
                    ("head_object", {"Bucket": "sample", "Key": "hello.txt"}),
                ]:
                    with self.subTest(access=access, operation=operation):
                        with self.assertRaises(ClientError) as error:
                            getattr(client, operation)(**kwargs)
                        self.assertEqual(error.exception.response["ResponseMetadata"]["HTTPStatusCode"], 403)
                        if operation != "head_object":
                            self.assertEqual(error.exception.response["Error"]["Code"], code)

    def test_unsigned_and_malformed_auth_rejected_before_resource_lookup(self):
        for path in ["/sample/hello.txt", "/sample?list-type=2", "/missing/key"]:
            for auth in [None, "AWS4-HMAC-SHA256 broken", "AWS access:signature"]:
                with self.subTest(path=path, auth=auth):
                    request = urllib.request.Request(self.endpoint + path)
                    if auth:
                        request.add_header("Authorization", auth)
                    self.assert_http_error(request, "AccessDenied")
        self.assert_http_error(urllib.request.Request(
            self.endpoint + "/sample/hello.txt", method="HEAD"), "AccessDenied")

    def test_signed_request_tampering_rejected(self):
        for path, changed in [("/sample/hello.txt", "/sample/dir/a.txt"),
                              ("/sample?list-type=2", "/sample?list-type=2&prefix=dir%2F")]:
            request = self.signed_request(path)
            request.full_url = self.endpoint + changed
            self.assert_http_error(request, "SignatureDoesNotMatch")
        request = self.signed_request("/sample/hello.txt", headers={"Range": "bytes=0-4"})
        request.add_header("Range", "bytes=6-")
        self.assert_http_error(request, "SignatureDoesNotMatch")

    def test_expired_future_and_invalid_dates_rejected(self):
        for delta in [-20, 20]:
            timestamp = (datetime.now(timezone.utc) + timedelta(minutes=delta)).strftime("%Y%m%dT%H%M%SZ")
            self.assert_http_error(self.signed_request("/sample/hello.txt", timestamp=timestamp),
                                   "RequestTimeTooSkewed")
        request = self.signed_request("/sample/hello.txt")
        request.add_header("X-Amz-Date", "invalid")
        self.assert_http_error(request, "AccessDenied")

    def test_query_plus_has_same_meaning_as_encoded_plus(self):
        request = self.signed_request("/sample?list-type=2&prefix=%2B")
        request.full_url = self.endpoint + "/sample?list-type=2&prefix=+"
        with urllib.request.urlopen(request, timeout=3) as response:
            result = ET.fromstring(response.read())
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        self.assertEqual(result.findtext("s3:Prefix", namespaces=ns), "+")
        self.assertEqual([node.text for node in result.findall("s3:Contents/s3:Key", ns)], ["+plus.txt"])

    def test_localhost_and_header_whitespace(self):
        raw = AWSRequest(method="GET", url=self.endpoint.replace("127.0.0.1", "localhost") + "/sample/hello.txt",
                         headers={"X-Test": "contains t and  spaces"})
        S3SigV4Auth(Credentials("test", "test-secret-한글"), "s3", "us-east-1").add_auth(raw)
        request = urllib.request.Request(raw.url, headers=dict(raw.headers))
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.read(), self.files["hello.txt"])

    def test_unsigned_payload_and_encoded_query(self):
        raw = AWSRequest(method="GET", url=self.endpoint + "/sample/hello.txt")
        raw.context["client_config"] = Config(s3={"payload_signing_enabled": False})
        S3SigV4Auth(Credentials("test", "test-secret-한글"), "s3", "us-east-1").add_auth(raw)
        self.assertEqual(raw.headers["X-Amz-Content-SHA256"], "UNSIGNED-PAYLOAD")
        request = urllib.request.Request(raw.url, headers=dict(raw.headers))
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.read(), self.files["hello.txt"])
        listing = self.client.list_objects_v2(Bucket="sample", Prefix="한글 + & <")
        self.assertEqual([o["Key"] for o in listing["Contents"]], ["한글 + & <test>.txt"])

    def test_missing_signed_headers_and_wrong_scope_rejected(self):
        for original, changed in [
            ("SignedHeaders=host;", "SignedHeaders="),
            ("/s3/aws4_request", "/sts/aws4_request"),
            ("/aws4_request", "/invalid"),
            ("Signature=", "Other="),
        ]:
            request = self.signed_request("/sample/hello.txt")
            auth = request.get_header("Authorization")
            self.assertIn(original, auth)
            request.add_header("Authorization", auth.replace(original, changed))
            self.assert_http_error(request, "AccessDenied")
        request = self.signed_request("/sample/hello.txt")
        request.remove_header("X-amz-content-sha256")
        self.assert_http_error(request, "AccessDenied")

    def test_download_example_reads_configuration(self):
        with tempfile.TemporaryDirectory() as output:
            dest = Path(output) / "download.txt"
            result = subprocess.run([
                os.sys.executable, "examples/download.py", "sample", "hello.txt", str(dest),
                "--endpoint", self.endpoint, "--config", str(self.config_path),
            ], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(dest.read_bytes(), self.files["hello.txt"])

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
        request = self.signed_request("/sample/new", method="PUT")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 405)
        error.exception.close()


class ConfigurationTest(unittest.TestCase):
    def test_invalid_configuration_prevents_startup(self):
        image = os.environ.get("S3_TEST_IMAGE", "local-s3-java8:test")
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "credentials.properties"
            for content in [None, "", "accessKey=test\n", "secretKey=secret\n",
                            "accessKey=test\nsecretKey=   \n", "accessKey=bad/key\nsecretKey=secret\n"]:
                with self.subTest(content=content):
                    if content is not None:
                        config.write_text(content)
                    command = ["docker", "run", "--rm", "--network", "none",
                               "--mount", "type=bind,src=" + directory + ",dst=/data,readonly"]
                    if content is not None:
                        command += ["--mount", "type=bind,src=" + str(config) + ",dst=/app/s3.properties,readonly"]
                    result = subprocess.run(command + [image], capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("Local S3:", result.stdout)
                    self.assertNotIn("secretKey=", result.stderr)

    def test_custom_configuration_path(self):
        image = os.environ.get("S3_TEST_IMAGE", "local-s3-java8:test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample").mkdir()
            (root / "sample" / "hello.txt").write_bytes(b"custom config")
            with tempfile.TemporaryDirectory() as config_directory:
                config = Path(config_directory) / "custom.properties"
                config.write_text("accessKey=custom\nsecretKey=custom-secret\n")
                container = subprocess.check_output([
                    "docker", "run", "--rm", "-d", "-p", "127.0.0.1::9000",
                    "--mount", "type=bind,src=" + directory + ",dst=/data,readonly",
                    "--mount", "type=bind,src=" + str(config) + ",dst=/custom.properties,readonly",
                    image, "/data", "9000", "0.0.0.0", "/custom.properties",
                ], text=True).strip()
                try:
                    port = subprocess.check_output(["docker", "port", container, "9000/tcp"], text=True).strip().rsplit(":", 1)[1]
                    endpoint = "http://127.0.0.1:" + port
                    for _ in range(100):
                        try:
                            urllib.request.urlopen(endpoint, timeout=1).close()
                            break
                        except urllib.error.HTTPError as error:
                            error.close()
                            break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        self.fail("Custom-config server did not start")
                    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                                          aws_access_key_id="custom", aws_secret_access_key="custom-secret",
                                          config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 0}))
                    try:
                        result = client.get_object(Bucket="sample", Key="hello.txt")
                        with result["Body"] as body:
                            self.assertEqual(body.read(), b"custom config")
                    finally:
                        client.close()
                finally:
                    subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, check=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
