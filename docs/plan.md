# 구현 및 검증 기록

현재 구현은 설정 파일 기반 SigV4 인증과 실행 가능한 JAR를 포함합니다.
아래 초기 구현 기록의 7개 테스트는 당시 결과이며, 후속 변경을 포함한 전체 테스트는 19개입니다.
현재 사용법은 [README](../README.md)와 [운영 가이드](operations.md), 구조는 [설계 문서](design.md)를 참고합니다.

## 후속 변경 완료

- [x] `src/S3Auth.java`: 외부 설정 파일의 키로 헤더 기반 AWS Signature V4 검증
- [x] 인증 누락·잘못된 키·요청 변조·시각 오차·설정 파일 오류 통합 검증
- [x] 다운로드 예제의 설정 파일 읽기와 별도 설정 경로 지원
- [x] `build.sh`: `build/local-s3.jar` 생성 및 `run.sh`·Docker의 JAR 실행
- [x] JAR 진입점·독립 실행·인증 파일 제외 검증
- [x] `sh tests/run.sh`: Java 8 Docker 환경에서 19개 테스트 통과(PR #2 병합 전 확인)
- [x] [PR #2](https://github.com/reiple/simple-s3-local/pull/2): 인증과 JAR 변경 main 병합
- [x] 한국어 실행·운영·설계 문서 및 문서 간 링크 보완

## 초기 구현 기록

아래 내용은 초기 읽기 전용 API 구현 당시의 계획과 검증 이력입니다.

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
