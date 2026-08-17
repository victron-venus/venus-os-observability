#!/bin/bash
# Build wheel bundle for Venus OS (ARMv7/aarch64)
# Run on Linux ARM or use docker buildx for cross-compile

set -euo pipefail

PKG_NAME="venus-os-observability"
VERSION=$(grep '^version' pyproject.toml | cut -d'"' -f2)
OUT_DIR="dist/wheels-${VERSION}"

echo "Building wheel bundle for ${PKG_NAME} v${VERSION}"

# Clean
rm -rf "${OUT_DIR}" dist/*.whl

# Build project wheel
pip wheel --no-deps -w "${OUT_DIR}" .

# Download dependency wheels (ARM compatible)
pip wheel -w "${OUT_DIR}" \
  opentelemetry-api==1.21.0 \
  opentelemetry-sdk==1.21.0 \
  opentelemetry-exporter-prometheus==1.21.0 \
  opentelemetry-exporter-otlp==1.21.0 \
  opentelemetry-instrumentation-requests==0.42b0 \
  opentelemetry-instrumentation-paho-mqtt==0.42b0 \
  prometheus-client==0.19.0 \
  paho-mqtt==2.0.0 \
  dbus-next==0.2.3 \
  pyyaml==6.0.1 \
  structlog==24.1.0 \
  click==8.1.7

# Create tarball
tar -czf "dist/${PKG_NAME}-wheels-${VERSION}.tar.gz" -C "${OUT_DIR}" .

echo "Done: dist/${PKG_NAME}-wheels-${VERSION}.tar.gz"
echo "Upload to GitHub Releases"
