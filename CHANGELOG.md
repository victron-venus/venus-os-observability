# Changelog

## [0.1.9] - 2026-10-14

### Changed

- Validate each public release candidate with bounded, network-isolated fuzz campaigns against its exact source revision.
- Require positive engine execution counts, instrumented production code and validated seed corpora; retain source-bound reports and diagnostic evidence.
- Run deterministic seed and configuration checks in ordinary CI without starting mutation campaigns on betas.

### Upgrade

Use the existing installation procedure, preserve local configuration and keep the previous accepted package for rollback. This maintenance release updates release validation and does not change the application configuration format. Deployment and applicable device or integration acceptance remain separate from publication.

### Security

The release gate rejects absent or invalid fuzz evidence and unsafe seed inputs. No application vulnerability or new target-device acceptance is claimed by these tooling changes.

## [0.1.8] - Development line

### Release candidate validation

- Require bounded fuzzing on public release candidates, with source-bound reports and retained diagnostics; beta and ordinary CI do not run mutation campaigns.

### Changes

- Bound and validate Grafana webhook bodies and entire alert batches before forwarding notifications; document governance, security evidence and remaining OpenSSF requirements.
- Reject oversized, entity-bearing or malformed D-Bus introspection XML without interrupting service discovery.
- Close alert TLS sockets after fatal handshake errors while preserving nonblocking retries.
- Verify SMTP STARTTLS certificates and reject weak alert-relay TLS keys before sending credentials or alert data.
- Add explicit HTTPS OTLP/HTTP export with certificate and key checks; reject HTTPS gRPC with migration guidance.
- Verify locked Python dependency hashes across CI, packaging and container builds, retaining the supported Paho MQTT API.
- Separate metric routing predicates without changing published values, refresh build/security tooling, and fix release API status handling.

### Upgrade

Grafana webhook clients must supply one decimal Content-Length, stay within 1 MiB/1000 alerts, and use string labels/annotations. Empty alerts arrays remain supported; malformed batches are rejected before delivery. Rebuild alert relay environments with the locked TLS dependencies and replace certificates below the documented key minima. Migrate HTTPS gRPC exporters to an explicitly configured HTTPS OTLP/HTTP listener with `http/protobuf`; explicit local plaintext gRPC remains supported. See [the OTLP migration guide](docs/otlp-transport.md) and [alert relay TLS guidance](docs/alert-relay-tls.md). Hardware and in-place device upgrades require separate acceptance checks.

### Security

Verify peer certificates, hostnames and minimum key sizes before sending SMTP credentials, alert content or HTTPS OTLP data. Reject unsafe redirects and proxy routes. Replace weak certificates and retain explicit trust configuration; local plaintext exporters remain an operator-selected trust boundary.

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
