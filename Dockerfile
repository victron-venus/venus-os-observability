# Ubuntu 24.04 supplies Python 3.12 and matching native D-Bus/GI bindings.
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3 AS builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --only-binary :all: uv==0.11.31 \
    && uv build --wheel --out-dir /tmp/wheels \
    && uv pip install --python /opt/venv/bin/python --no-cache --only-binary :all: /tmp/wheels/*.whl

FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3
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
