# Local S3 API design

Java 8 standard-library HTTP server, compiled with javac without Maven or Gradle.
The root contains bucket directories, whose regular files map to object keys.
Implement path-style GetObject and ListObjectsV2, with minimal HeadObject for
boto3 download_file. GET streams bytes; HEAD supplies length, ETag and modification
time. Single byte ranges support parallel downloads without loading files in memory.

ListObjectsV2 supports prefix, delimiter, max-keys (0..1000), start-after,
continuation-token and encoding-type=url. Return S3 XML and XML errors.
Pagination counts both objects and CommonPrefixes. Sort keys by unsigned UTF-8 bytes.
Do not follow symlinks, allow traversal or expose files outside a bucket.

Authentication is intentionally omitted; use dummy boto3 credentials and path-style
addressing. Bind Docker's published port to loopback and mount local data read-only.
No uploads, deletes, bucket management, virtual-host addressing or general S3 parity.
Files should remain unchanged during downloads and pagination.

Validate using an actual Java 8 Docker image and boto3 integration tests, including
multipart download, Unicode names, pagination, ranges, empty files and error cases.
