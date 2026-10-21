# 로컬 S3 API 설계

## 구성과 실행

Java 8 표준 라이브러리의 `HttpServer`로 구현한 읽기 전용 path-style S3 서버입니다.
외부 Java 라이브러리와 Maven/Gradle 없이 `javac`로 컴파일하고 `jar`로 패키징합니다.
`build.sh`는 `Main-Class: S3Server`를 가진 `build/local-s3.jar`를 생성합니다.
`run.sh`와 Docker 런타임 모두 `java -jar`로 실행합니다.
데이터와 인증 설정은 JAR·Docker 이미지 밖에서 제공합니다.

| 파일 | 역할 |
| --- | --- |
| `src/S3Server.java` | HTTP 처리, 파일 경로 보호, 스트리밍, Range, 목록·XML 응답 |
| `src/S3Auth.java` | 설정 파일 로딩, AWS Signature V4 검증 |
| `build.sh`, `run.sh`, `Dockerfile` | JAR 빌드 및 실행 |
| `examples/download.py` | 설정 파일 기반 boto3 목록 조회·다운로드 |
| `tests/test_s3.py` | 실제 Docker 서버에 대한 boto3 통합 검증 |
| `tests/test_packaging.py` | 독립 실행 JAR 생성·실행 검증 |

## 요청 처리와 인증

시작 시 UTF-8 설정 파일에서 `accessKey`, `secretKey`를 읽습니다.
기본 경로는 `s3.properties`이며 네 번째 실행 인자로 변경할 수 있습니다.
키가 누락되거나 유효하지 않으면 서버를 시작하지 않습니다. 실행 중 설정 재로딩은 하지 않습니다.

요청 처리 순서는 다음과 같습니다.

1. 요청 ID 생성
2. 리소스 접근 전에 `Authorization` 기반 AWS Signature V4 검증
3. HTTP 메서드와 버킷·객체 경로, 쿼리 확인
4. ListObjectsV2 또는 GetObject·HeadObject 처리
5. XML 오류 또는 객체 바이트 응답

access key를 비교하고 secret key로 canonical request와 서명을 다시 계산합니다.
서명 비교에는 `MessageDigest.isEqual`을 사용합니다. secret key는 요청·오류 응답에 포함하지 않습니다.
서명 시각은 서버와 15분 이내여야 하며 scope의 날짜·서비스(`s3`)를 확인합니다.
region은 서명 계산에 반영하지만 특정 값으로 제한하지 않습니다.
영구 키 한 쌍만 지원하며 presigned URL·SigV2·임시 세션 토큰은 지원하지 않습니다.
인증 실패는 S3 오류 코드와 HTTP 403으로 응답합니다. HEAD 오류에는 본문이 없습니다.

## 파일과 목록

데이터 루트의 하위 디렉터리가 버킷이며, 버킷 아래 일반 파일의 상대 경로가 객체 키입니다.
심볼릭 링크, 경로 탐색, 역슬래시 등을 거부해 데이터 루트 밖의 파일 접근을 제한합니다.
인증 설정은 데이터 루트 밖에 배치해야 합니다.

GET은 전체 파일을 메모리에 올리지 않고 스트리밍합니다.
HEAD는 boto3 다운로드에 필요한 크기·ETag·Last-Modified를 제공합니다.
단일 byte Range와 If-Match를 지원해 병렬 다운로드와 파일 식별을 처리합니다.
ETag는 크기와 수정 시각을 이용한 식별자이며 MD5가 아닙니다.

ListObjectsV2는 prefix, delimiter, max-keys(0~1000), start-after,
continuation-token, encoding-type=url을 지원합니다.
UTF-8 바이트 순으로 정렬하며 CommonPrefixes도 페이지 항목 수에 포함합니다.
페이지 토큰은 버킷·prefix·delimiter와 연결됩니다.
서명 계산과 쿼리 해석에서 `+`는 리터럴 플러스, 공백은 `%20`으로 일관되게 처리합니다.

목록 요청마다 디렉터리 전체를 스캔하고 정렬하므로 소규모 로컬 파일 제공에 적합합니다.
페이지 처리·다운로드 중 원본 파일이 변경되지 않는 것을 전제로 합니다.
TLS, 업로드·삭제, 버킷 관리, 가상 호스트 주소 및 전체 S3 기능 호환은 구현하지 않습니다.

## 검증

Java 8 Docker 이미지에서 실제 boto3 요청으로 검증합니다.
인증 실패와 요청 변조, 설정 오류, 한글·특수문자 키, 대용량 병렬 다운로드,
목록 페이지·prefix·delimiter, HEAD·Range, 경로·심볼릭 링크·권한 오류를 포함합니다.
패키징 테스트는 JAR 생성, 진입점, 인증 설정 제외, 별도 클래스 없이 실행을 확인합니다.

사용·운영 절차는 [운영 가이드](operations.md), 구현 이력은 [구현 기록](plan.md)을 참고합니다.
