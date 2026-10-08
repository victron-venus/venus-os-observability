"""Real loopback OTLP requests, including a calibrated weak-chain server.

All keys are temporary. The low security level is confined to the fixture and
its CA/hostname-verifying oracle; the product client keeps its own TLS policy.
"""

import http.client
import os
import select
import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from venus_observability.otlp import _tls_context, _verify_key_lengths, create_exporter

Key = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
CHAIN_CASES = (
    "strong",
    "strong-ec",
    "weak-leaf",
    "weak-intermediate",
    "weak-root",
    "weak-2047-root",
    "weak-ec-root",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith("OTEL_EXPORTER_OTLP") or name.upper().endswith("_PROXY"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")


def certificate(
    key: Key, name: str, issuer: x509.Certificate | None, issuer_key: Key, *, ca: bool
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
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
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
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), False
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), False
        ).add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope="module")
def chains(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, Path, Path]]:
    directory = tmp_path_factory.mktemp("otlp-synthetic-pki")
    result = {}
    for case in CHAIN_CASES:
        root_bits = {"weak-root": 1024, "weak-2047-root": 2047}.get(case, 2048)
        root_key: Key
        if case in ("strong-ec", "weak-ec-root"):
            curve = ec.SECP192R1() if case == "weak-ec-root" else ec.SECP256R1()
            root_key = ec.generate_private_key(curve)
        else:
            root_key = rsa.generate_private_key(65537, root_bits)
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
        cert_file, key_file, ca_file = (
            directory / f"{case}.{suffix}" for suffix in ("pem", "key", "ca")
        )
        cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + intermediate)
        key_file.write_bytes(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ca_file.write_bytes(root.public_bytes(serialization.Encoding.PEM))
        result[case] = cert_file, key_file, ca_file
    return result


@contextmanager
def peer(
    chain: tuple[Path, Path, Path],
    version: ssl.TLSVersion,
    *,
    redirect: str | None = None,
    client_auth: bool = False,
) -> Iterator[tuple[int, dict[str, Any]]]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(chain[0], chain[1])
    if client_auth:
        context.load_verify_locations(chain[2])
        context.verify_mode = ssl.CERT_REQUIRED
    result: dict[str, Any] = {"application_bytes": b""}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as connection:
                        result["tls"] = connection.version()
                        data = b""
                        while b"\r\n\r\n" not in data:
                            part = connection.recv(8192)
                            if not part:
                                if not data:
                                    result["closed_before_http"] = True
                                    return
                                raise EOFError("Expected HTTP headers")
                            data += part
                            result["application_bytes"] = data
                        headers, body = data.split(b"\r\n\r\n", 1)
                        length = next(
                            int(line.split(b":", 1)[1])
                            for line in headers.split(b"\r\n")
                            if line.lower().startswith(b"content-length:")
                        )
                        while len(body) < length:
                            part = connection.recv(8192)
                            if not part:
                                raise EOFError("Expected complete OTLP body")
                            body += part
                        result["application_bytes"] = headers + b"\r\n\r\n" + body
                        response = (
                            f"HTTP/1.1 307 Temporary Redirect\r\nLocation: {redirect}\r\n"
                            if redirect
                            else "HTTP/1.1 200 OK\r\n"
                        )
                        connection.sendall(
                            (response + "Content-Length: 0\r\nConnection: close\r\n\r\n").encode()
                        )
            except ssl.SSLError as error:
                result["handshake_error"] = str(error)
            except ConnectionResetError as error:
                if result["application_bytes"]:
                    result["unexpected_error"] = repr(error)
                else:
                    result["closed_before_http"] = True
            except Exception as error:
                result["unexpected_error"] = repr(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], result
        finally:
            thread.join(6)
            assert not thread.is_alive()
            assert "unexpected_error" not in result, result


def calibrate(chain: tuple[Path, Path, Path], version: ssl.TLSVersion) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_verify_locations(chain[2])
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    with peer(chain, version) as (port, observed):
        connection = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
        try:
            connection.request("POST", "/oracle", body=b"calibration")
            assert connection.getresponse().status == 200
        finally:
            connection.close()
    assert observed["application_bytes"].endswith(b"calibration")


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize("case", [*CHAIN_CASES, "untrusted", "wrong-host"])
def test_actual_exporter_verifies_entire_chain_before_sending(
    chains: dict[str, tuple[Path, Path, Path]],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    version: ssl.TLSVersion,
) -> None:
    chain = chains.get(case, chains["strong"])
    calibrate(chain, version)
    ca = chains["strong-ec"][2] if case == "untrusted" else chain[2]
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_CERTIFICATE", str(ca))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer synthetic-only")
    with peer(chain, version) as (port, observed):
        host = "127.0.0.1" if case == "wrong-host" else "localhost"
        exporter: Any = create_exporter(f"https://{host}:{port}")
        try:
            if case.startswith("strong"):
                response = exporter._export(b"synthetic-span")
                assert response.status_code == 200
                response.close()
            else:
                with pytest.raises(requests.exceptions.SSLError):
                    exporter._export(b"synthetic-span")
        finally:
            exporter.shutdown()
    if case.startswith("strong"):
        assert b"authorization: Bearer synthetic-only" in observed["application_bytes"]
        assert observed["application_bytes"].endswith(b"synthetic-span")
        assert observed["tls"] == version.name.replace("_", ".")
    else:
        assert observed["application_bytes"] == b""


def test_redirect_does_not_send_auth_or_body_to_plaintext_target(
    chains: dict[str, tuple[Path, Path, Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_CERTIFICATE", str(chains["strong"][2]))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer synthetic-only")
    with socket.socket() as target:
        target.bind(("127.0.0.1", 0))
        target.listen(1)
        target.settimeout(0.2)
        redirect = f"http://127.0.0.1:{target.getsockname()[1]}/capture"
        with peer(chains["strong"], ssl.TLSVersion.TLSv1_3, redirect=redirect) as (port, _):
            exporter: Any = create_exporter(f"https://localhost:{port}")
            try:
                with pytest.raises(requests.RequestException, match="redirects are disabled"):
                    exporter._export(b"synthetic-span")
            finally:
                exporter.shutdown()
        with pytest.raises(TimeoutError):
            target.accept()


def test_standard_client_certificate_configuration_is_preserved(
    chains: dict[str, tuple[Path, Path, Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    cert, key, ca = chains["strong"]
    for name, path in (("CERTIFICATE", ca), ("CLIENT_CERTIFICATE", cert), ("CLIENT_KEY", key)):
        monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{name}", str(path))
    with peer(chains["strong"], ssl.TLSVersion.TLSv1_3, client_auth=True) as (port, observed):
        exporter: Any = create_exporter(f"https://localhost:{port}")
        try:
            response = exporter._export(b"mutual-tls")
            assert response.status_code == 200
            response.close()
        finally:
            exporter.shutdown()
    assert observed["application_bytes"].endswith(b"mutual-tls")


@pytest.mark.parametrize("level", [0, 3])
def test_tls_floor_preserves_stricter_defaults(level: int) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_ciphers(f"DEFAULT:@SECLEVEL={level}")
    with patch("venus_observability.otlp.ssl.SSLContext", return_value=context):
        actual = _tls_context()
    assert actual.minimum_version == ssl.TLSVersion.TLSv1_3
    assert actual.security_level == max(level, 2)


@contextmanager
def proxy(
    target_port: int, chain: tuple[Path, Path, Path] | None
) -> Iterator[tuple[int, list[bytes]]]:
    """A bounded CONNECT relay whose only permitted destination is our loopback peer."""
    observed: list[bytes] = []
    errors: list[Exception] = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    if chain:
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        context.set_ciphers("DEFAULT:@SECLEVEL=0")
                        context.load_cert_chain(chain[0], chain[1])
                        incoming: socket.socket = context.wrap_socket(raw, server_side=True)
                    else:
                        incoming = raw
                    with incoming:
                        request = b""
                        while b"\r\n\r\n" not in request:
                            data = incoming.recv(8192)
                            if not data:
                                return
                            request += data
                        observed.append(request)
                        assert request.startswith(f"CONNECT localhost:{target_port} ".encode())
                        with socket.create_connection(("127.0.0.1", target_port), timeout=5) as out:
                            incoming.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                            while True:
                                ready, _, _ = select.select([incoming, out], [], [], 5)
                                if not ready:
                                    raise TimeoutError("Synthetic proxy did not finish")
                                for source in ready:
                                    data = source.recv(8192)
                                    if not data:
                                        return
                                    destination = out if source is incoming else incoming
                                    destination.sendall(data)
            except ssl.SSLError:
                # TLS clients may reject this proxy certificate or close its tunnel.
                pass
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], observed
        finally:
            thread.join(6)
            assert not thread.is_alive()
            assert not errors, errors


@pytest.mark.parametrize("encrypted_proxy", [False, True])
@pytest.mark.parametrize("case", ["strong", "weak-2047-root"])
def test_proxy_cannot_bypass_origin_chain_policy(
    chains: dict[str, tuple[Path, Path, Path]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    encrypted_proxy: bool,
    case: str,
) -> None:
    bundle = tmp_path / "roots.pem"
    bundle.write_bytes(chains["strong"][2].read_bytes() + chains[case][2].read_bytes())
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_CERTIFICATE", str(bundle))
    with (
        peer(chains[case], ssl.TLSVersion.TLSv1_3) as (port, received),
        proxy(port, chains["strong"] if encrypted_proxy else None) as (proxy_port, observed),
    ):
        scheme = "https" if encrypted_proxy else "http"
        monkeypatch.setenv("HTTPS_PROXY", f"{scheme}://localhost:{proxy_port}")
        exporter: Any = create_exporter(f"https://localhost:{port}")
        try:
            if case == "strong":
                response = exporter._export(b"proxy-span")
                assert response.status_code == 200
                response.close()
            else:
                with pytest.raises(
                    requests.exceptions.ProxyError, match="OTLP HTTPS transport failed"
                ):
                    exporter._export(b"proxy-span")
        finally:
            exporter.shutdown()
    assert len(observed) == 1
    assert received["application_bytes"].endswith(b"proxy-span") == (case == "strong")


def test_weak_https_proxy_rejected_before_connect_request(
    chains: dict[str, tuple[Path, Path, Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_CERTIFICATE", str(chains["weak-2047-root"][2]))
    # No origin server is needed: the unaccepted proxy must not receive CONNECT.
    with proxy(1, chains["weak-2047-root"]) as (port, observed):
        monkeypatch.setenv("HTTPS_PROXY", f"https://localhost:{port}")
        exporter: Any = create_exporter("https://localhost:1")
        try:
            with pytest.raises(requests.exceptions.ProxyError, match="OTLP HTTPS transport failed"):
                exporter._export(b"must-not-leave")
        finally:
            exporter.shutdown()
    assert observed == []


@pytest.mark.parametrize("sock", [object(), SimpleNamespace(get_verified_chain=lambda: [])])
def test_missing_verified_chain_fails_closed(sock: object) -> None:
    with pytest.raises(ssl.SSLError, match="verified TLS chain"):
        _verify_key_lengths(sock)


@pytest.mark.parametrize("case", ["strong", "weak-2047-root"])
def test_public_der_chain_api_also_checks_exact_bits(
    chains: dict[str, tuple[Path, Path, Path]], case: str
) -> None:
    certificate = x509.load_pem_x509_certificate(chains[case][2].read_bytes())
    der = certificate.public_bytes(serialization.Encoding.DER)
    sock = SimpleNamespace(get_verified_chain=lambda: [der])
    if case == "strong":
        _verify_key_lengths(sock)
    else:
        with pytest.raises(ssl.SSLError, match="security minimum"):
            _verify_key_lengths(sock)
