# 실행 및 운영 가이드

이 서버는 로컬 디렉터리의 파일을 읽기 전용 S3 API로 제공합니다.
처음 실행할 때는 [README](../README.md)의 데이터·인증 설정과 Docker 예제를 따릅니다.

## 준비물과 배포 파일

| 작업 | 필요 도구 |
| --- | --- |
| 소스에서 JAR 빌드 | JDK 8 이상: `javac`, `jar` |
| JAR 실행 | Java 8 이상 |
| Docker 빌드·실행 | Docker |
| 다운로드 예제 | Python 3, `requirements-test.txt`의 boto3 |
| 전체 테스트 | Docker, Python 3, uv 또는 venv/pip |

배포할 파일은 `local-s3.jar`, 외부 인증 설정 파일, 데이터 디렉터리입니다.
JAR는 `sh build.sh`로 `build/local-s3.jar`에 생성됩니다.
호스트에 JDK가 없으면 README의 Docker JAR 추출 절차를 사용합니다.
빌드된 JAR에는 클래스와 실행 진입점이 포함되며 별도 클래스패스 설정은 필요하지 않습니다.

```text
/opt/local-s3/
├── local-s3.jar
├── s3.properties
└── data/
    └── demo/
        └── docs/hello.txt
```

이 배치에서 버킷은 `demo`, 객체 키는 `docs/hello.txt`입니다.
빈 디렉터리는 객체로 표시되지 않으며 심볼릭 링크는 제공하지 않습니다.

## 인증 설정

[s3.properties.example](../s3.properties.example)을 복사하고 두 값을 변경합니다.

```properties
accessKey=local
secretKey=local
```

- UTF-8 `key=value` 형식을 사용합니다. 예제 클라이언트와 공유할 때는 한 줄에 값을 쓰고 역슬래시 이스케이프·줄 연결·값 끝 공백을 피합니다.
- `accessKey`는 영문·숫자·점·밑줄·하이픈, `secretKey`는 비어 있지 않은 값이어야 합니다.
- 환경변수로 인증 키를 읽지 않습니다. 설정 파일이 없거나 필수 키가 유효하지 않으면 시작이 실패합니다.
- 파일을 데이터 루트 밖에 두고 서버 실행 계정에 읽기 권한을 줍니다. 예: `chmod 600 s3.properties`.
- 기본 이름 `s3.properties`는 Git에서 제외됩니다. 다른 이름으로 설정 파일을 만들면 직접 Git 추적 제외 여부를 확인합니다.
- 파일은 시작할 때 한 번 읽습니다. 키를 바꾸면 서버를 재시작하고 클라이언트에도 같은 키를 적용합니다.

## JAR 실행 인자

```sh
java -jar /opt/local-s3/local-s3.jar /opt/local-s3/data 9000 127.0.0.1 /opt/local-s3/s3.properties
```

| 순서 | 인자 | 기본값 | 설명 |
| --- | --- | --- | --- |
| 1 | 데이터 루트 | 필수 | 버킷 디렉터리를 포함하는 기존 디렉터리 |
| 2 | 포트 | `9000` | HTTP 수신 포트 |
| 3 | 바인딩 주소 | `127.0.0.1` | 서버가 수신할 주소 |
| 4 | 설정 파일 | `s3.properties` | 인증 키 파일 경로 |

뒤쪽 인자를 생략할 수 있습니다. 설정 파일만 별도로 지정하려면 앞선 포트와 바인딩 주소도 적습니다.
직접 `java -jar`로 실행할 때 상대 데이터·설정 경로는 **현재 작업 디렉터리** 기준입니다.
JAR 파일이 있는 디렉터리 기준으로 자동 변경되지 않습니다.

`sh run.sh`는 프로젝트 루트로 이동한 뒤 같은 인자 순서로 JAR를 실행합니다.
데이터 루트를 생략하면 `./data`를 사용하며, 상대 경로는 **프로젝트 루트** 기준입니다.
JAR가 없으면 빌드하지만 소스 수정 시 자동 재빌드하지 않으므로 `sh build.sh`를 다시 실행합니다.

## Docker에서 다른 설정 경로 사용

프로젝트 루트에 `data`와 `s3.properties`를 준비한 뒤 실행합니다.

```sh
docker build -t local-s3-java8 .
docker run --rm --name local-s3 \
  -p 127.0.0.1:9000:9000 \
  --mount "type=bind,src=$(pwd)/data,dst=/data,readonly" \
  --mount "type=bind,src=$(pwd)/s3.properties,dst=/config/s3.properties,readonly" \
  local-s3-java8 /data 9000 0.0.0.0 /config/s3.properties
```

이미지 뒤의 인자는 JAR에 전달됩니다. 경로는 컨테이너 내부 경로를 사용합니다.
컨테이너 내부 바인딩은 `0.0.0.0`, 호스트 공개 포트는 `127.0.0.1`로 설정합니다.
Docker 기본 설정 파일 경로는 `/app/s3.properties`입니다.
설정 파일은 이미지에 포함되지 않으므로 읽기 전용으로 마운트해야 합니다.

## 목록 조회 및 다운로드 확인

[다운로드 예제](../examples/download.py)는 목록을 조회한 뒤 파일을 다운로드합니다.
프로젝트 루트에서 boto3를 설치하고 실행합니다.

```sh
.venv/bin/python examples/download.py demo docs/hello.txt downloads/hello.txt \
  --endpoint http://127.0.0.1:9000 \
  --config ./s3.properties
```

`--endpoint` 기본값은 `http://127.0.0.1:9000`, `--config` 기본값은 `s3.properties`입니다.
예제의 상대 경로도 현재 작업 디렉터리 기준입니다.
직접 boto3를 사용할 때는 path-style 주소와 `signature_version='s3v4'`를 설정합니다.
클라이언트를 생성하는 것만으로 서버에 접속하거나 인증을 확인하지는 않습니다.
`list_objects_v2`, `get_object`, `head_object` 등 실제 요청이 실행될 때 키를 검증합니다.

## 오류 대응

| 증상 | 확인할 내용 |
| --- | --- |
| 시작 시 설정 파일을 찾을 수 없음 | 설정 경로, 현재 작업 디렉터리, Docker 마운트 경로 확인 |
| `Credential configuration requires...` | `accessKey` 형식, `secretKey` 누락·빈 값 확인 |
| `AccessDenied` (403) | 무서명 요청, 잘못된 인증 형식, 지원하지 않는 세션 토큰 확인. 경로·파일 권한 문제도 같은 코드를 반환할 수 있음 |
| `InvalidAccessKeyId` (403) | 서버 설정과 클라이언트의 access key 일치 여부 확인 |
| `SignatureDoesNotMatch` (403) | secret key와 서명 후 경로·쿼리·헤더 변경 여부 확인 |
| `RequestTimeTooSkewed` (403) | 서버·클라이언트 시각을 확인. 허용 오차는 15분 |
| `NoSuchBucket` / `NoSuchKey` (404) | 데이터 루트, 버킷 디렉터리, 객체 키 확인 |
| `InvalidArgument` (400) | 목록 인자·인코딩·페이지 토큰 확인 |
| `InvalidRange` (416) | 파일 크기를 벗어난 Range 또는 빈 파일 Range 확인 |
| `NotImplemented` (501) | 지원 API와 객체 하위 리소스 여부 확인 |
| `MethodNotAllowed` (405) | 정상 인증된 요청에서 GET·HEAD 이외의 메서드 사용 여부 확인 |

인증은 리소스 조회와 메서드 처리보다 먼저 실행됩니다.
HEAD 오류는 본문이 없으므로 클라이언트에서 구체적인 S3 오류 코드 대신 HTTP 상태만 보일 수 있습니다.
무서명 `curl` 요청이 403을 반환하는 것은 정상이며, 위 boto3 예제로 인증된 동작을 확인합니다.

## 종료·변경 및 지원 한계

포그라운드 JAR는 Ctrl+C로 종료합니다. Docker는 `docker stop local-s3`로 종료합니다.
코드 변경 시 JAR 또는 Docker 이미지를 다시 빌드하고 재시작합니다.
데이터 디렉터리는 읽기 전용으로 제공하며 다운로드·목록 페이지 처리 중 원본 파일을 변경하지 않는 것을 전제로 합니다.

영구 키 한 쌍의 헤더 기반 SigV4를 지원합니다.
presigned URL, SigV2, 임시 세션 토큰, TLS, 업로드·삭제, 버킷 관리, 가상 호스트 주소는 지원하지 않습니다.
전체 API 지원 범위는 [README](../README.md)를 참고합니다.
