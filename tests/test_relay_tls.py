"""Actual relay SMTP/HTTPS connections with disposable verified certificate chains."""

import importlib.util
import os
import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from test_smtp_tls import serve_smtp

Key = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
CASES = (
    "strong",
    "strong-ec",
    "weak-leaf",
    "weak-intermediate",
    "weak-root",
    "rsa2047-root",
    "ec192-root",
)


def certificate(
    key: Key, name: str, issuer: x509.Certificate | None, signer: Key, *, ca: bool
) -> x509.Certificate:
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=not ca,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), False
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), False
        ).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
    return builder.sign(signer, hashes.SHA256())


@pytest.fixture(scope="module")
def relay_chains(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, Path, Path]]:
    directory = tmp_path_factory.mktemp("relay-pki")
    result = {}
    for case in CASES:
        root_key: Key
        if case in ("strong-ec", "ec192-root"):
            root_key = ec.generate_private_key(
                ec.SECP192R1() if case == "ec192-root" else ec.SECP256R1()
            )
        else:
            root_key = rsa.generate_private_key(
                65537, {"weak-root": 1024, "rsa2047-root": 2047}.get(case, 2048)
            )
        root = certificate(root_key, case, None, root_key, ca=True)
        issuer, issuer_key = root, root_key
        intermediate = b""
        if case == "weak-intermediate":
            issuer_key = rsa.generate_private_key(65537, 1024)
            issuer = certificate(issuer_key, "intermediate", root, root_key, ca=True)
            intermediate = issuer.public_bytes(serialization.Encoding.PEM)
        leaf_key: Key = (
            ec.generate_private_key(ec.SECP256R1())
            if case == "strong-ec"
            else rsa.generate_private_key(65537, 1024 if case == "weak-leaf" else 2048)
        )
        leaf = certificate(leaf_key, "localhost", issuer, issuer_key, ca=False)
        cert, key, ca = (directory / f"{case}.{suffix}" for suffix in ("pem", "key", "ca"))
        cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + intermediate)
        key.write_bytes(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        key.chmod(0o600)
        ca.write_bytes(root.public_bytes(serialization.Encoding.PEM))
        result[case] = cert, key, ca
    return result


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in os.environ:
        if name.upper().endswith("_PROXY"):
            monkeypatch.delenv(name)
    path = Path(__file__).resolve().parents[1] / "alert-mqtt-bridge" / "relay.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("tls_test_relay", path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SMTP_HOST = "localhost"
    module.SMTP_USER = "synthetic-user"
    module.SMTP_PASS = "synthetic-password"
    module.SMTP_FROM = "sender@example.invalid"
    module.SMTP_TO = ["recipient@example.invalid"]
    module.TG_BOT_TOKEN = "synthetic-token"
    module.TG_CHAT_IDS = ["synthetic-chat"]
    return module


@contextmanager
def peer(
    chain: tuple[Path, Path, Path],
    version: ssl.TLSVersion,
    *,
    smtp: bool = False,
    connect: bool = False,
    redirect: str | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    context.set_ciphers("DEFAULT:@SECLEVEL=0")  # Only the synthetic peer permits weak fixtures.
    context.load_cert_chain(chain[0], chain[1])
    result: dict[str, Any] = {"commands": [], "application": b"", "connect": b""}
    failures = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    if smtp:
                        serve_smtp(raw, context, result["commands"], True)
                        return
                    if connect:
                        while b"\r\n\r\n" not in result["connect"]:
                            chunk = raw.recv(8192)
                            if not chunk:
                                raise EOFError("Expected CONNECT headers")
                            result["connect"] += chunk
                        raw.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    with context.wrap_socket(raw, server_side=True) as conn:
                        result["tls"] = conn.version()
                        while b"\r\n\r\n" not in result["application"]:
                            chunk = conn.recv(8192)
                            if not chunk:
                                return
                            result["application"] += chunk
                        location = f"Location: {redirect}\r\n" if redirect else ""
                        code = "302 Found" if redirect else "200 OK"
                        conn.sendall(
                            (
                                f"HTTP/1.1 {code}\r\n{location}"
                                "Content-Length: 2\r\nConnection: close\r\n\r\nOK"
                            ).encode()
                        )
            except (ssl.SSLError, ConnectionResetError):
                pass  # Client rejection is separately checked against a successful TLS oracle.
            except Exception as error:
                failures.append(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], result
        finally:
            thread.join(6)
            assert not thread.is_alive(), "Local peer did not stop"
            assert not failures, failures


def trust(monkeypatch: pytest.MonkeyPatch, chain: tuple[Path, Path, Path]) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", str(chain[2]))
    monkeypatch.setenv("SSL_CERT_DIR", str(chain[2].parent / "no-system-roots"))


def local_telegram(relay: Any, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    actual_open = relay.open_https

    def open_local(requested: str, **kwargs: Any) -> Any:
        assert requested == "https://api.telegram.org/botsynthetic-token/sendMessage"
        return actual_open(url, **kwargs)

    monkeypatch.setattr(relay, "open_https", open_local)
    relay.send_telegram("fixture", "warning", "Synthetic summary", "Synthetic body")


@pytest.mark.parametrize("case", CASES + ("wrong-host", "untrusted"))
@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize("channel", ["smtp", "telegram"])
def test_relay_checks_chain_before_credentials(
    relay: Any,
    relay_chains: dict[str, tuple[Path, Path, Path]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    version: ssl.TLSVersion,
    channel: str,
) -> None:
    chain = relay_chains["strong" if case in ("wrong-host", "untrusted") else case]
    # Calibrate every offered chain using normal CA/hostname checks, lowering only
    # this disposable oracle's key-size policy. A fixture handshake must succeed.
    oracle = ssl.create_default_context(cafile=str(chain[2]))
    oracle.set_ciphers("DEFAULT:@SECLEVEL=0")
    oracle.minimum_version = oracle.maximum_version = version
    with (
        peer(chain, version) as (port, calibration),
        socket.create_connection(("127.0.0.1", port), timeout=3) as raw,
        oracle.wrap_socket(raw, server_hostname="localhost") as conn,
    ):
        assert conn.version() == ("TLSv1.2" if version == ssl.TLSVersion.TLSv1_2 else "TLSv1.3")
        conn.sendall(b"GET /calibration HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert conn.recv(8192).startswith(b"HTTP/1.1 200")
    assert calibration["application"]
    trust(monkeypatch, relay_chains["strong-ec"] if case == "untrusted" else chain)
    host = "127.0.0.1" if case == "wrong-host" else "localhost"
    with peer(chain, version, smtp=channel == "smtp") as (port, observed):
        if channel == "smtp":
            relay.SMTP_HOST, relay.SMTP_PORT = host, port
            relay.send_email("fixture", "warning", "Synthetic summary", "Synthetic body")
        else:
            local_telegram(relay, monkeypatch, f"https://{host}:{port}/fixture")
    accepted = case in ("strong", "strong-ec")
    if channel == "smtp":
        assert (b"AUTH" in observed["commands"]) is accepted
        assert (b"DATA" in observed["commands"]) is accepted
    else:
        assert bool(observed["application"]) is accepted
    assert "synthetic-password" not in caplog.text
    assert "synthetic-token" not in caplog.text


def test_https_proxy_rejected_before_connect(relay: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.1)
        monkeypatch.setenv(
            "HTTPS_PROXY",
            f"https://synthetic-user:synthetic-password@127.0.0.1:{listener.getsockname()[1]}",
        )
        relay.send_telegram("fixture", "warning", "Synthetic summary", "Synthetic body")
        with pytest.raises(TimeoutError):
            listener.accept()


def test_unused_https_proxy_preserves_no_proxy(
    relay: Any, relay_chains: dict[str, tuple[Path, Path, Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = relay_chains["strong"]
    trust(monkeypatch, chain)
    monkeypatch.setenv("HTTPS_PROXY", "https://unused.invalid:9")
    monkeypatch.setenv("NO_PROXY", "localhost")
    with peer(chain, ssl.TLSVersion.TLSv1_2) as (port, observed):
        local_telegram(relay, monkeypatch, f"https://localhost:{port}/fixture")
    assert observed["application"]


def test_http_proxy_keeps_verified_tunnel(
    relay: Any, relay_chains: dict[str, tuple[Path, Path, Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = relay_chains["strong"]
    trust(monkeypatch, chain)
    with peer(chain, ssl.TLSVersion.TLSv1_2, connect=True) as (port, observed):
        monkeypatch.setenv(
            "HTTPS_PROXY", f"http://synthetic-user:synthetic-password@127.0.0.1:{port}"
        )
        local_telegram(relay, monkeypatch, "https://localhost:443/fixture")
    assert observed["connect"].startswith(b"CONNECT localhost:443")
    assert b"Proxy-Authorization:" in observed["connect"]
    assert b"Proxy-Authorization:" not in observed["application"]
    assert observed["application"]


def test_http_redirect_rejected_before_request(
    relay: Any, relay_chains: dict[str, tuple[Path, Path, Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = relay_chains["strong"]
    trust(monkeypatch, chain)
    with socket.socket() as insecure:
        insecure.bind(("127.0.0.1", 0))
        insecure.listen(1)
        insecure.settimeout(0.1)
        with peer(
            chain,
            ssl.TLSVersion.TLSv1_2,
            redirect=f"http://127.0.0.1:{insecure.getsockname()[1]}/unsafe",
        ) as (port, observed):
            local_telegram(relay, monkeypatch, f"https://localhost:{port}/fixture")
        assert observed["application"]
        with pytest.raises(TimeoutError):
            insecure.accept()


def test_https_redirect_still_works(
    relay: Any, relay_chains: dict[str, tuple[Path, Path, Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = relay_chains["strong"]
    trust(monkeypatch, chain)
    with (
        peer(chain, ssl.TLSVersion.TLSv1_2) as (target, second),
        peer(chain, ssl.TLSVersion.TLSv1_2, redirect=f"https://localhost:{target}/final") as (
            port,
            first,
        ),
    ):
        local_telegram(relay, monkeypatch, f"https://localhost:{port}/fixture")
    assert first["application"].startswith(b"POST /fixture")
    assert second["application"].startswith(b"GET /final")


@pytest.mark.parametrize(
    "connection", [object(), type("EmptyChain", (), {"get_verified_chain": lambda self: []})()]
)
def test_missing_verified_chain_fails_closed(relay: Any, connection: Any) -> None:
    import tls_policy

    with pytest.raises(ssl.SSLError, match="verified certificate chain"):
        tls_policy._check_verified_keys(connection)


def test_stricter_context_settings_are_preserved(
    relay: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tls_policy

    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_ciphers("DEFAULT:@SECLEVEL=3")
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)
    assert tls_policy.verified_context() is context
    assert context.minimum_version == ssl.TLSVersion.TLSv1_3
    assert context.security_level == 3
