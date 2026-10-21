#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -f build/classes/S3Server.class ]; then
    sh build.sh
fi
exec java -cp build/classes S3Server "${1:-./data}" "${2:-9000}" "${3:-127.0.0.1}" "${4:-s3.properties}"
