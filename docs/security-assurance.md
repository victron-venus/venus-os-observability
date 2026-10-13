# Security assurance case

This is a bounded argument for the reviewed implementation, not a penetration-test certificate or a guarantee of device safety. Reassess it whenever dependencies, inputs or deployment assumptions change.

## Assets, actors and trust boundaries

Assets include service credentials, household/device telemetry, accurate alert routing, repository publication rights and availability of the observed system. Potential attackers include a network peer able to reach listeners, an untrusted D-Bus sender, a compromised collector or broker, and a malicious dependency or contributor.

The agent reads Venus D-Bus events and exposes Prometheus metrics. Optional OTLP export sends spans to a configured collector. A separate HTTP alert bridge accepts Grafana JSON and forwards notifications to MQTT and optional SMTP/Telegram. Neither component is an independent safety control. D-Bus signals and alerts must not directly authorize physical actions.

The deployment boundary requires an authorized operator, protected local configuration and a trusted or isolated network. Prometheus and the alert webhook do not implement user authentication. Plaintext MQTT and explicitly configured local gRPC do not protect telemetry in transit. Restrict ingress with firewall/container networking and an authenticated TLS proxy where remote access is required; never expose these listeners directly to the Internet. Proxy authentication over a plaintext HTTP CONNECT hop has separate confidentiality risks described in the transport guides.

## Claims and supporting evidence

- **D-Bus attribution and availability:** [owner lifecycle tests](../tests/test_owner_lifecycle.py), [metric invalidation tests](../tests/test_owner_metric_invalidation.py) and [invalid batch tests](../tests/test_invalid_dbus_batch.py) exercise owner changes, invalidation and malformed values. Discovery rejects introspection text over 1,048,576 characters and entity declarations, handles malformed XML, and preserves the standard external D-Bus DOCTYPE without fetching it. This does not detect every silent source outage.
- **Bounded alert input:** the [relay](../alert-mqtt-bridge/relay.py) requires one decimal Content-Length, rejects transfer encoding, limits an HTTP body to 1 MiB and a batch to 1000 alerts, uses a read timeout, and validates the complete batch before dispatch. [Regression tests](../tests/test_alert_bridge.py) exercise malformed JSON, lengths and late invalid entries. This is not authentication or a global connection/rate limit; the deployment boundary remains required.
- **Authenticated outbound TLS:** [OTLP TLS tests](../tests/test_otlp_tls.py), [relay TLS tests](../tests/test_relay_tls.py) and [SMTP tests](../tests/test_smtp_tls.py) use disposable local peers. Certificate trust, hostname, exact key minima and redirect rejection are checked before application secrets are sent. See [OTLP transport](otlp-transport.md) and [alert TLS](alert-relay-tls.md) for supported runtime assumptions, proxies and migration.
- **Reviewed delivery:** locked dependencies, CodeQL, Bandit, Trivy and required CI support change review. GitHub HTTPS protects source/download transport. The current stable v0.1.7 has checksums but no verified artifact signature; there is no project-wide signed-release or reproducible-build claim.
- **Least privilege:** the companion [Dockerfile](../Dockerfile) selects a non-root user. Native setup needs installation privileges and retains the platform's supervisor model. No audit of all native binary mitigations or claim that arbitrary container launch settings are hardened is made.

## Cryptography and credentials

Cryptography is delegated to maintained FLOSS Python/OpenSSL, Requests, urllib3 and cryptography components. SHA-256 in release tooling detects payload changes. TLS uses OS/library cryptographic randomness; application correlation UUIDs are identifiers, not authentication tokens or keys. The project does not store password verifiers for authenticating external users.

Supported protected transports require TLS 1.2 or later and exact certificate minima: RSA 2048 actual bits, EC 224 bits or DSA parameters 2048/224; Ed25519 and Ed448 are supported. Weak or unavailable verified chains fail closed. The same check applies to supplied OTLP client certificates. Client key files are loaded from a captured snapshot through private temporary files, which are removed. No certificate bypass is enabled.

Credentials and client certificates are operator-owned configuration, never committed examples. Replace them and restart the relevant component through the normal operational process. The project does not provide a unified dedicated credential-file mechanism for every channel; environment-held secrets and plaintext MQTT remain limitations. Keep configuration readable only by the intended account and redact shared logs/captures.

## Assurance limitations

First-party implementation is Python and shell; no project-maintained C/C++ source is included. Dependencies include native libraries whose memory safety is not established by Python tests. No sanitizer result, complete branch/statement coverage, continuous production fuzzing, third-party security audit or all-platform acceptance is claimed. [Quality and build evidence](quality-and-build.md) distinguishes the actual checks from these gaps. Outstanding findings are triaged under [SECURITY.md](../SECURITY.md).
