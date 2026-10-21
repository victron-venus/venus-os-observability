FROM python:3.15-alpine@sha256:2699b90f4d562a22e2dd076c82f964032f64814204bff36d271a54ba4cc2c7e2 AS builder
WORKDIR /build
COPY requirements-build.txt requirements.txt requirements-tls.txt ./
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements-build.txt \
    && python -m pip download --no-cache-dir --require-hashes --only-binary=:all: --dest /wheels -r requirements-tls.txt \
    && python -m pip wheel --no-cache-dir --require-hashes --no-deps --no-build-isolation --wheel-dir /wheels -r requirements.txt

FROM python:3.15-alpine@sha256:2699b90f4d562a22e2dd076c82f964032f64814204bff36d271a54ba4cc2c7e2
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --no-deps /wheels/*.whl \
    && rm -rf /wheels
COPY relay.py tls_policy.py /app/
WORKDIR /app
RUN addgroup -S relay && adduser -S -G relay relay
USER relay
EXPOSE 8095
CMD ["python3", "-u", "relay.py"]
