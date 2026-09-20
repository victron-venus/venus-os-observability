# Venus OS Observability - Docker

# Build stage
FROM python:3.11-slim-bookworm AS builder

WORKDIR /app
RUN pip install --no-cache-dir --only-binary :all: uv==0.11.31

COPY pyproject.toml README.md ./
COPY src ./src

RUN uv build --wheel --out-dir /tmp/wheels \
    && uv pip install --system --no-cache --only-binary :all: /tmp/wheels/*.whl

# Runtime stage
FROM python:3.11-slim-bookworm

# The listener uses dbus-python and GLib. Bookworm's bindings have the same
# CPython 3.11 ABI as this image; dbus-next does not provide these modules.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-dbus python3-gi \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/usr/lib/python3/dist-packages

WORKDIR /app

# Copy installed packages
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin

# Non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/metrics', timeout=5).close()" || exit 1

# Expose Prometheus metrics port
EXPOSE 9090

# Entry point
ENTRYPOINT ["python", "-m", "venus_observability"]
