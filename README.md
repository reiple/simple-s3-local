# Java 8 로컬 파일 S3 API

Java 표준 라이브러리 `HttpServer`만 사용하는 읽기 전용 서버입니다.
Maven/Gradle이나 외부 Java 라이브러리 없이 `javac`로 컴파일합니다.

## Docker 실행 (호스트에 Java 불필요)

프로젝트 루트에서 실행합니다.

```sh
mkdir -p data/demo/docs
printf 'hello local S3\n' > data/demo/docs/hello.txt

docker build -t local-s3-java8 .
docker run --rm --name local-s3 \
  -p 127.0.0.1:9000:9000 \
  --mount "type=bind,src=$(pwd)/data,dst=/data,readonly" \
  local-s3-java8
```

`eclipse-temurin:8-jdk`에서 컴파일하고 `eclipse-temurin:8-jre`에서 실행합니다.
포그라운드 실행이므로 다른 터미널에서 boto3 예제를 실행합니다.
종료는 Ctrl+C 또는 `docker stop local-s3`입니다.

```text
data/                         ← 데이터 루트
└── demo/                     ← Bucket=demo
    └── docs/hello.txt         ← Key=docs/hello.txt
```

파일은 실제 S3에 저장하지 않습니다. 로컬 버킷 디렉터리 아래 일반 파일만
목록에 표시하며, 파일 바이트를 스트리밍합니다. 빈 디렉터리는 객체로 표시하지 않습니다.

## boto3 목록 조회 및 다운로드

```sh
# uv가 있는 경우
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-test.txt

# 또는 Python venv/pip 사용
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt

.venv/bin/python examples/download.py demo docs/hello.txt downloads/hello.txt
```

직접 호출하려면 다음처럼 설정합니다. 인증을 검증하지 않으므로 키는 임의 값입니다.

```python
import boto3
from botocore.config import Config

s3 = boto3.client(
    's3',
    endpoint_url='http://127.0.0.1:9000',
    region_name='us-east-1',
    aws_access_key_id='local',
    aws_secret_access_key='local',
    config=Config(s3={'addressing_style': 'path'}),
)

for page in s3.get_paginator('list_objects_v2').paginate(Bucket='demo'):
    for obj in page.get('Contents', []):
        print(obj['Key'], obj['Size'])

s3.download_file('demo', 'docs/hello.txt', 'hello.txt')
s3.close()
```

`get_object()`와 `download_fileobj()`도 사용할 수 있습니다.
큰 파일은 boto3 기본 전송 설정에 따라 Range 요청으로 병렬 다운로드됩니다.

## Java 1.8이 설치된 환경

```sh
sh build.sh
sh run.sh ./data 9000 127.0.0.1
```

`run.sh` 인자는 데이터 루트, 포트, 바인딩 주소 순서입니다.
기본값은 `./data`, `9000`, `127.0.0.1`입니다. 상대 데이터 경로는 프로젝트 루트 기준입니다.
코드 변경 후에는 `sh build.sh`를 다시 실행합니다.

## 지원 범위

| API | 지원 기능 |
| --- | --- |
| GetObject | 전체 스트리밍, 단일 byte Range, If-Match |
| HeadObject | boto3 다운로드에 필요한 크기, ETag, Last-Modified |
| ListObjectsV2 | prefix, delimiter, max-keys, start-after, continuation-token, encoding-type=url |

- 경로 형식은 `/bucket/key`만 지원합니다. Virtual-host 방식은 지원하지 않습니다.
- 목록은 UTF-8 바이트 순으로 정렬합니다. `max-keys`는 0~1000이며
  `CommonPrefixes`도 페이지의 항목 수에 포함됩니다.
- 페이지 토큰은 동일 버킷·prefix·delimiter에서 재사용해야 합니다.
- 없는 버킷/파일은 404, 금지된 경로는 403, 잘못된 인자는 400,
  범위를 벗어난 Range는 416, 지원하지 않는 HTTP 메서드는 405를 반환합니다.
- 심볼릭 링크를 따르지 않으며 `.`/`..`, 빈 경로 요소와 역슬래시를 포함한 키는 거부합니다.
  역슬래시가 있는 파일명은 목록에서도 제외합니다.
- ETag는 파일 크기와 수정 시각으로 생성한 메타데이터 식별자이며 MD5 해시가 아닙니다.
- 목록 조회마다 디렉터리 전체를 스캔·정렬합니다. 소규모 로컬 파일 제공 용도입니다.
- 다운로드·페이지 처리 중 원본 파일을 변경하지 않는 것을 전제로 합니다.
- 인증/서명 검증, TLS, 업로드/삭제, 버킷 관리, 버전 관리, ACL, 체크섬 API는 구현하지 않습니다.
  인증이 없는 로컬 테스트용 서버이므로 예제는 포트를 loopback으로 제한합니다.

프로토콜 참고: [AWS ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html),
[boto3 다운로드](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-example-download-file.html).

## 테스트

Docker, Python 3, 그리고 `uv` 또는 `venv`/`pip`가 필요합니다.

```sh
sh tests/run.sh
```

스크립트는 Java 8 이미지를 빌드하고 임시 데이터를 읽기 전용으로 마운트해
boto3 통합 테스트를 실행합니다. 컨테이너 포트는 loopback의 임의 포트로 할당되며
테스트가 끝나면 컨테이너와 임시 데이터를 삭제합니다.

검증 내용: 일반/한글/특수문자/빈 파일 다운로드, 약 9MB 파일의 병렬 다운로드와
SHA-256 비교, 목록·prefix·delimiter·페이지 처리, HEAD/Range/If-Match,
없는 파일·잘못된 인자·경로 탐색·심볼릭 링크 거부, 파일 읽기 권한 오류 응답.
