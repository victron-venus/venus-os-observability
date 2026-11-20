# Ubuntu 24.04 supplies Python 3.12 and matching native D-Bus/GI bindings.
FROM ubuntu:24.04@sha256:9c7f200c07e3d4a03e7249d2c16bf66a51120fb876aa2242cf31451412543289 AS builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY .github/requirements-release-build.txt .github/requirements-runtime.txt ./.github/
RUN python3 -m venv /opt/build-venv \
    && /opt/build-venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r .github/requirements-release-build.txt \
    && /opt/build-venv/bin/python -m build --no-isolation --wheel --outdir /tmp/wheels . \
    && python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r .github/requirements-runtime.txt \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps --no-index /tmp/wheels/*.whl

FROM ubuntu:24.04@sha256:9c7f200c07e3d4a03e7249d2c16bf66a51120fb876aa2242cf31451412543289
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-dbus python3-gi ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser
# Validate native ABI compatibility without connecting to the host bus.
RUN python -c 'import dbus; from gi.repository import GLib; import venus_observability'
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/metrics', timeout=5).close()" || exit 1
EXPOSE 9090
ENTRYPOINT ["python", "-m", "venus_observability"]
