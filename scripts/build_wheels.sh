#!/bin/bash
# Run with the same Python ABI and CPU architecture as the target Venus OS.
# This downloads wheels; it does not cross-compile native dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
PKG_NAME="venus-os-observability"
VERSION=$("$PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
OUT_DIR="dist/wheels-${VERSION}"
mkdir -p "$OUT_DIR"
# Resolve one dependency graph from project metadata; stale hand-written OTel
# versions included nonexistent releases and nonexistent MQTT instrumentation.
"$PYTHON" -m pip wheel --no-deps -w "$OUT_DIR" .
"$PYTHON" -m pip download --only-binary=:all: --dest "$OUT_DIR" \
    "$OUT_DIR/${PKG_NAME//-/_}-${VERSION}-py3-none-any.whl"
# Bootstrap pip offline even when Venus OS has no ensurepip.
"$PYTHON" -m pip download --only-binary=:all: --dest "$OUT_DIR" pip
tar -czf "dist/${PKG_NAME}-wheels-${VERSION}.tar.gz" -C "$OUT_DIR" .
echo "Created dist/${PKG_NAME}-wheels-${VERSION}.tar.gz"
