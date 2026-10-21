#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -f build/local-s3.jar ]; then
    sh build.sh
fi
exec java -jar build/local-s3.jar "${1:-./data}" "${2:-9000}" "${3:-127.0.0.1}" "${4:-s3.properties}"
