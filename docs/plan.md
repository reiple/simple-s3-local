# Local S3 implementation plan

**Goal:** Download local files via boto3 using a Java 8 S3-compatible endpoint.
**Spec:** [design.md](design.md)
**Execution:** In this session, following the user's instruction to proceed.

## Constraints
- Java 1.8 standard library; javac only, no Maven/Gradle.
- Docker for Java 8 build and execution because host Java is absent.
- Read-only local file mapping; GetObject, ListObjectsV2 and minimal HeadObject.

## Files and steps
- [x] `tests/test_s3.py`: real boto3 tests against Docker and temporary local fixtures.
  Prove listing/download tests fail against a not-yet-implemented endpoint.
- [x] `src/S3Server.java`: HTTP dispatch, streaming GET/HEAD, safe path resolution,
  ranges, metadata, listing XML and opaque continuation tokens.
- [x] `Dockerfile`, `build.sh`, `run.sh`: Java 8 compilation and execution.
- [x] `tests/run.sh`, `requirements-test.txt`: reproducible integration suite.
- [x] `examples/download.py`, `README.md`: local/Docker usage and supported scope.
- [x] Run all integration tests against Java 8; inspect implementation and artifacts.

## Review focus
Tests cover Unicode/plus/XML names, empty and multipart downloads, prefix pagination
including CommonPrefixes, invalid ranges/query parameters, traversal and symlinks.

## Verification record
- Initial stub returned 501: integration tests failed as expected.
- Java runtime: Temurin 1.8.0_504; compilation inside the Java 8 JDK image.
- Final review found pre-header I/O errors and listing/GET key eligibility mismatch.
  Reproduced in tests, fixed with explicit response state and shared restrictions.
- `sh tests/run.sh`: 7 tests pass, including non-root permission errors.
- `sh -n build.sh run.sh tests/run.sh`: pass.
- No existing git repository or branch; work stays in the user-provided empty workspace.
- Ruling: use installed uv for test dependencies because host venv lacks ensurepip.
