FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS builder
WORKDIR /build
COPY requirements-build.txt requirements.txt ./
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements-build.txt \
    && python -m pip wheel --no-cache-dir --require-hashes --no-deps --no-build-isolation --wheel-dir /wheels -r requirements.txt

FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --no-deps /wheels/*.whl \
    && rm -rf /wheels
COPY relay.py /app/relay.py
WORKDIR /app
RUN addgroup -S relay && adduser -S -G relay relay
USER relay
EXPOSE 8095
CMD ["python3", "-u", "relay.py"]
