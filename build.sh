#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p build/classes
javac -encoding UTF-8 -source 1.8 -target 1.8 -d build/classes src/*.java
