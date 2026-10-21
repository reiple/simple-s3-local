#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker build -t local-s3-java8:test .
if command -v uv >/dev/null 2>&1; then
    uv venv --allow-existing .venv
    uv pip install --python .venv/bin/python -r requirements-test.txt
else
    if [ ! -x .venv/bin/python ]; then
        python3 -m venv .venv
    fi
    .venv/bin/python -m pip install -r requirements-test.txt
fi
S3_TEST_IMAGE=local-s3-java8:test .venv/bin/python -m unittest discover -s tests -v
