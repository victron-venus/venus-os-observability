# Venus OS Observability - Docker

```dockerfile
# Build stage
FROM python:3.11-slim as builder

WORKDIR /app
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src ./src

RUN uv pip install --system --no-cache -e .

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin

# Non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:9090/metrics || exit 1

# Expose Prometheus metrics port
EXPOSE 9090

# Entry point
ENTRYPOINT ["python", "-m", "venus_observability"]
```

```yaml
# docker-compose.yml - Full stack for local development
version: "3.8"

services:
  venus-observability:
    build: .
    container_name: venus-observability
    environment:
      - DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
      - PROMETHEUS_PORT=9090
      - LOG_LEVEL=DEBUG
    volumes:
      - /run/dbus:/run/dbus
    ports:
      - "9090:9090"
    depends_on:
      - tempo
    restart: unless-stopped

  tempo:
    image: grafana/tempo:2.4
    container_name: tempo
    command:
      - -config.file=/etc/tempo.yaml
    volumes:
      - ./tempo.yaml:/etc/tempo.yaml
      - tempo-data:/var/tempo
    ports:
      - "3200:3200"
      - "4317:4317"  # OTLP gRPC
      - "4318:4318"  # OTLP HTTP
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:v2.48
    container_name: prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    ports:
      - "9091:9090"
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.2
    container_name: grafana
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning
      - ./grafana/dashboards:/var/lib/grafana/dashboards
      - grafana-data:/var/lib/grafana
    ports:
      - "3000:3000"
    depends_on:
      - prometheus
      - tempo
    restart: unless-stopped

volumes:
  tempo-data:
  prometheus-data:
  grafana-data:
```