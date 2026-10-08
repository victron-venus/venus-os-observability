# Changelog

## [0.1.7] - Development line

- Harden outbound alert relay TLS: reject undersized keys in the verified chain before SMTP authentication or Telegram data, and reject unprotected proxy/redirect routes. See `docs/alert-relay-tls.md`.

### Release overview

Exports D-Bus/MQTT observations to OpenTelemetry and Prometheus. The existing README documents configuration and external interfaces for this development line.

### Maintenance

- Add explicitly selected HTTPS OTLP/HTTP export with certificate-key minimums, verified names and CAs, and no redirects. Reject HTTPS gRPC with a migration instruction; retain explicit local plaintext gRPC.
- Verify hashes for locked CI, security-tool, release-build and container Python dependencies; build local packages without resolving an isolated backend.
- Preserve the alert relay’s Paho MQTT 1.6.1 API while verifying its source archive and building it with a locked backend in a separate container stage.
- Separate metric path predicates from publication while preserving gauge labels, unavailable-value handling and service invalidation.
- Publish reviewed release notes from the exact source commit used to build each candidate, preserving build provenance.
- Document contribution checks, confidential security reporting and the project-specific trust boundaries.

### Upgrade

Alert relay users must install the additional hash-locked TLS dependencies or rebuild the relay image. Reissue certificates below the documented key minima. HTTPS-proxy URLs are rejected; configure a direct route, NO_PROXY or an explicit HTTP CONNECT proxy with its documented metadata exposure. See `docs/alert-relay-tls.md`.

Contributors should recreate their check environment with `bash scripts/ci.sh --install` after updating the lock and exported requirements together. Container builders require supported prebuilt dependency wheels. Existing HTTPS gRPC exporters must migrate to the collector's explicitly configured HTTPS OTLP/HTTP listener and select `http/protobuf`; see [the transport migration guide](docs/otlp-transport.md). Local plaintext gRPC configuration is unchanged. Retain local configuration and credentials when using the documented update procedure. Validate the candidate on an isolated system before production use; automated checks do not establish hardware acceptance.

### Security

SMTP and Telegram now check exact keys on the verified connection before authentication or request data. This fixes acceptance of RSA-2047 roots under the tested OpenSSL security level. Telegram also refuses HTTP redirects and unprotected HTTPS-proxy handling.

Private vulnerability reporting and response policy are documented in SECURITY.md. The previous gRPC TLS backend accepted trusted chains containing RSA keys below 2048 bits on the tested runtime. HTTPS export now uses OpenSSL security level 2 or higher plus exact key-length checks on the same verified connection, and rejects redirects before sending data to another endpoint. This does not replace deployment authentication, network isolation or independent equipment safeguards. No new project CVE is announced by these changes.

## 0.1.5 - 2026-09-12

- Resolve D-Bus senders to stable Venus service names using asynchronous startup
  discovery and `NameOwnerChanged`; remove superseded owners instead of retaining
  transient process names in metric labels and the owner cache.
- Preserve startup signal batches until their service is resolved, with a bounded
  queue, expiry counter and rate-limited diagnostic. Retry failed discovery without
  blocking the GLib loop or scanning all owners for every measurement.
- Mark previously published Prometheus and OpenTelemetry gauges unavailable when
  their service loses or replaces its owner; keep stable counters cumulative and
  recover on fresh values, including valid zero readings.
- Register the message filter once across subscriptions and cancel owner lookups,
  pending batches and timer callbacks on shutdown. Include the new owner tracker
  in the validated native SetupHelper archive.

The alert relay, deployment configuration and installer behavior are unchanged.

## 0.1.4 - 2026-09-12

- Avoid recording D-Bus trace payloads when tracing is disabled and normalize
  D-Bus string attributes when tracing is enabled.
- Export invalid numeric D-Bus values as unavailable without dropping valid
  measurements in the same signal; preserve real zero readings.
- Load public helpers lazily and shut down telemetry only once.
- Preserve service and logger supervisor directories during repeat installs,
  check runtime dependencies before stopping the service, and place the boot
  hook before `exit 0` in `/data/rc.local`.
- Publish a validated SetupHelper source archive with SHA-256 checksums instead
  of invoking the unsupported historical IPK recipe.
- Publish wheel and source distributions on GitHub even when optional PyPI
  credentials are absent; report a skipped PyPI publication explicitly.

The source archive uses existing firmware D-Bus/GLib bindings and an existing
compatible Python environment. It does not bundle dependencies or provide a
universal ARM wheel set. Prepare matching offline wheels before a first install
or a Python ABI change. Container releases remain companion-host artifacts.
