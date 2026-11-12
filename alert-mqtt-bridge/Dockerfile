FROM python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd AS builder
WORKDIR /build
COPY requirements-build.txt requirements.txt requirements-tls.txt ./
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements-build.txt \
    && python -m pip download --no-cache-dir --require-hashes --only-binary=:all: --dest /wheels -r requirements-tls.txt \
    && python -m pip wheel --no-cache-dir --require-hashes --no-deps --no-build-isolation --wheel-dir /wheels -r requirements.txt

FROM python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --no-deps /wheels/*.whl \
    && rm -rf /wheels
COPY relay.py tls_policy.py /app/
WORKDIR /app
RUN addgroup -S relay && adduser -S -G relay relay
USER relay
EXPOSE 8095
CMD ["python3", "-u", "relay.py"]
