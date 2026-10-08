# OTLP transport and migration

Tracing is optional. Install the `tempo` extra from a matching, reviewed offline
dependency bundle. Without an OTLP endpoint, the service does not import either
OTLP transport and continues to provide metrics without exporting spans.

## Local gRPC

The existing configuration remains plaintext gRPC:

```sh
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
```

Use this only on an intentionally trusted local network or container network.
It does not protect trace contents or credentials in transit. Setting an HTTPS
endpoint with `grpc` now fails before telemetry providers or listeners are
registered. The tested gRPC native backend accepted trusted certificate chains
containing RSA keys below 2048 bits; its public Python API does not expose the
required complete certificate-key policy. HTTPS is not downgraded to plaintext.

## HTTPS OTLP/HTTP

Configure a TLS-enabled **OTLP/HTTP protobuf** listener on the collector, then
explicitly select it. Do not assume the gRPC listener accepts HTTP protobuf or
that replacing a port creates a TLS listener.

```sh
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example:4318
```

The generic endpoint is a base URL: the exporter appends `/v1/traces`, retaining
any base path. To provide a complete trace URL instead, set
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://collector.example/custom/traces`.
The trace-specific protocol variable `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` also
takes precedence over its generic equivalent. No automatic protocol or port
translation is performed. URL userinfo and fragments are rejected.

The exporter uses a private Requests session with normal certificate-chain and
hostname verification, TLS 1.2 or newer, and OpenSSL security level **at least 2**.
Stricter runtime defaults are retained. The supported runtime is CPython 3.12
with OpenSSL and the locked Requests/urllib3/cryptography dependencies. After
normal TLS verification, an additional check examines the **same connection's
verified chain**, including its trust anchor, before sending HTTP. It checks
exact RSA modulus length (at least 2048 bits), EC keys (at least 224 bits), and
DSA parameters (at least 2048-bit p and 224-bit q); Ed25519 and Ed448 are also
supported. Unknown key types fail closed. This exact check matters because
OpenSSL security level 2 alone can accept a 2047-bit RSA key.

CPython 3.12 exposes this chain through its private `_sslobj.get_verified_chain`
API. The adapter also recognizes the public DER-chain API introduced in Python
3.13, but that does not extend this package's supported Python version range.
A missing or empty verified-chain API fails closed. There is no separate
preflight connection, custom ASN.1 parser or bypass of ordinary trust/hostname
verification. This does not certify arbitrary replacement TLS libraries,
collector configurations or physical devices.

All HTTP redirects are rejected, including redirects to another HTTPS URL.
Configure the final collector URL directly. A redirect cannot forward trace
contents or authorization to a plaintext target. Startup logging does not print
the configured endpoint, because URLs can contain credentials in query strings.

Standard OTLP/HTTP environment settings remain available:

- `OTEL_EXPORTER_OTLP_CERTIFICATE` selects a trusted CA file. The trace-specific
  `OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE` takes precedence. Normal Requests trust
  configuration applies when neither is supplied; verification cannot be disabled.
- `OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE` and `OTEL_EXPORTER_OTLP_CLIENT_KEY`
  configure mutual TLS, with their `TRACES_` equivalents taking precedence.
- `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_OTLP_TIMEOUT` and
  `OTEL_EXPORTER_OTLP_COMPRESSION` retain their upstream trace-specific precedence.

Keep credentials in protected local configuration. Requests' HTTP/HTTPS CONNECT
proxy environment handling is retained; the adapter checks the origin chain
through the tunnel, and checks an HTTPS proxy's own verified chain before
sending CONNECT. SOCKS proxies are unsupported and fail clearly. Operators
remain responsible for the proxy/collector deployment and its access controls.

## Existing deployment migration

1. Enable and verify the collector's HTTPS OTLP/HTTP listener, trusted certificate
   and authorization requirements. Preserve the existing gRPC listener until
   other clients have been accounted for.
2. Update the offline `tempo` dependency bundle and the service package together.
3. Set the explicit `http/protobuf` protocol and the real HTTPS base or trace URL.
   Keep or adapt CA, client-certificate and authorization settings for that listener.
4. Restart through the operator's normal process and verify traces arrive. On
   failure, check the listener and transport configuration; do not disable
   verification or switch a remote endpoint to plaintext.

The software update does not deploy collectors, rotate credentials or change
live configuration. The native source archive includes this guide.
