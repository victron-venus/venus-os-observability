# Alert relay TLS policy

The standalone `alert-mqtt-bridge` verifies the exact public-key sizes of the
certificate chain accepted on its SMTP STARTTLS and Telegram HTTPS connections.
It checks the same connection before SMTP AUTH, email content or an HTTP request
is sent. Ordinary certificate-authority and hostname checks run first. There is
no second probe connection, alternate trust store or verification bypass.

Every certificate, including the selected trust anchor, requires RSA of at least
2048 actual bits, EC of at least 224 bits, or DSA parameters of at least 2048/224
bits. Ed25519 and Ed448 are recognized. Unknown algorithms and unavailable or
empty verified-chain metadata reject delivery. OpenSSL security level 2 alone
can accept a 2047-bit RSA modulus; the additional check closes that boundary.
TLS 1.2 is the minimum and stricter default security settings are retained.

The supported container retains the pinned Python 3.14 Alpine image and
Paho-MQTT 1.6.1. Its certificate parser and dependencies are hash locked in
`alert-mqtt-bridge/requirements-tls.txt` and installed from wheels. Local source
execution must install that file as well as the existing Paho requirement.
CPython 3.12 uses its private `_sslobj.get_verified_chain` interface; newer
runtimes may provide the public interface. Regression tests must run when
updating Python or the TLS libraries. No fallback skips the check.

## Trust, proxies and redirects

Each delivery creates a fresh standard SSL context. System trust and the
`SSL_CERT_FILE`/`SSL_CERT_DIR` overrides retain their normal Python behavior.
Correctly install a new CA bundle or reissue weak server/CA certificates to
restore delivery. Do not disable verification. SMTP still requires STARTTLS
before authentication; the relay does not add implicit SMTPS or client-certificate
configuration.

Telegram retains urllib's environment proxy selection and `NO_PROXY` bypasses.
An `https://` proxy URL is rejected before connecting or sending proxy credentials:
stdlib urllib does not establish TLS to that proxy before its CONNECT request.
Use a direct route, `NO_PROXY`, or a deliberately configured HTTP CONNECT proxy.
An HTTP proxy sees CONNECT metadata and any proxy authentication in plaintext;
the destination HTTPS connection is still verified before Telegram data is sent.
An unused HTTPS proxy setting is permitted when `NO_PROXY` bypasses it.

Telegram delivery rejects every redirect before contacting its target, including
HTTPS-to-HTTPS redirects. This prevents a POST from becoming a bodyless GET that
could incorrectly be reported as a delivered alert. The original endpoint receives
the POST once; configure the final Telegram endpoint directly. No global urllib
opener or SSL defaults are modified.

## Verification scope

`tests/test_relay_tls.py` runs the actual relay methods against disposable
loopback SMTP and HTTPS peers. A separate CA- and hostname-verifying oracle
calibrates each synthetic certificate chain before checking production rejection.
The tests cover TLS 1.2/1.3, weak leaf/intermediate/root keys, RSA-2047, strong
RSA/EC, wrong hostnames, untrusted issuers, proxies and redirect behavior. Secrets
are synthetic and no real email, Telegram messages or device commands are sent.

These checks cover the two outbound alert channels. They do not add MQTT TLS,
authentication to the incoming webhook, or protections to an external proxy or
TLS terminator. They do not establish a whole-project OpenSSF assessment.

## Inbound webhook boundary

`POST /grafana` accepts a JSON object with an optional `alerts` array (at most 1000 entries). Each alert is an object; `labels` and `annotations`, if present, are string-to-string maps. `fingerprint`, `startsAt` and `valueString` are strings; supplied status values are `firing` or `resolved`. Unknown fields are retained/ignored for Grafana compatibility. The whole batch is validated before notifications start. An omitted alerts array is an empty batch; null or wrongly typed fields are rejected.

HTTP requires one nonnegative decimal `Content-Length` of at most 1 MiB and no `Transfer-Encoding`. Invalid shape/JSON/length returns 400, oversize returns 413, body read timeout returns 408, and an internal dispatch value error returns a generic 500 rather than blaming the submitted payload. The read timeout is 10 seconds. `/health` returns JSON MQTT connection state. Input rejection does not authenticate a sender or guarantee delivery; restrict access to trusted Grafana instances and provide network-level rate limits.
