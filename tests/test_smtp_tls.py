"""Exercise SMTP certificate checks over loopback using disposable credentials."""

import importlib.util
import shutil
import socket
import ssl
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate throwaway CAs and server certificates; no private key is committed."""
    root = tmp_path_factory.mktemp("smtp-certificates")
    executable = shutil.which("openssl")
    assert executable is not None, "Install the OpenSSL CLI to run the TLS regression tests"

    def openssl(*args: str) -> None:
        subprocess.run([executable, *args], cwd=root, check=True, capture_output=True)

    for name in ("trusted", "other"):
        openssl(
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={name} test CA",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.pem",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        )
    for name, hostname in (("valid", "localhost"), ("wrong-host", "other.invalid")):
        openssl(
            "req",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-subj",
            f"/CN={hostname}",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.csr",
        )
        (root / "extensions.cnf").write_text(
            f"subjectAltName=DNS:{hostname}\nbasicConstraints=CA:FALSE\n"
            "extendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature\n"
        )
        openssl(
            "x509",
            "-req",
            "-in",
            f"{name}.csr",
            "-CA",
            "trusted.pem",
            "-CAkey",
            "trusted.key",
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            "extensions.cnf",
            "-out",
            f"{name}.pem",
        )
    return root


def serve_smtp(
    connection: socket.socket, context: ssl.SSLContext, commands: list[bytes], offer_tls: bool
) -> None:
    """Implement only the SMTP exchange needed to observe TLS-before-AUTH ordering."""
    with connection:
        connection.settimeout(5)
        connection.sendall(b"220 localhost test SMTP\r\n")
        stream = connection.makefile("rb")
        try:
            while line := stream.readline():
                command = line.split(None, 1)[0].upper()
                commands.append(command)
                if command == b"EHLO":
                    tls = b"250-STARTTLS\r\n" if offer_tls else b""
                    connection.sendall(b"250-localhost\r\n" + tls + b"250 AUTH PLAIN\r\n")
                elif command == b"STARTTLS":
                    connection.sendall(b"220 Ready for TLS\r\n")
                    stream.close()
                    connection = context.wrap_socket(connection, server_side=True)
                    stream = connection.makefile("rb")
                elif command == b"AUTH":
                    connection.sendall(b"235 Authentication successful\r\n")
                elif command == b"DATA":
                    connection.sendall(b"354 Send message\r\n")
                    for data_line in stream:
                        if data_line == b".\r\n":
                            break
                    else:
                        raise EOFError("Client disconnected during message data")
                    connection.sendall(b"250 Message accepted\r\n")
                elif command == b"QUIT":
                    connection.sendall(b"221 Goodbye\r\n")
                    return
                else:
                    connection.sendall(b"250 OK\r\n")
        finally:
            stream.close()
            connection.close()


@contextmanager
def smtp_server(
    certificates: Path, server_name: str, offer_tls: bool
) -> Iterator[tuple[int, list[bytes]]]:
    """Accept exactly one local connection, then close all sockets and join the worker."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(
        certificates / f"{server_name}.pem", certificates / f"{server_name}.key"
    )
    commands: list[bytes] = []
    failures: list[Exception] = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                connection, _ = listener.accept()
                serve_smtp(connection, context, commands, offer_tls)
            except ssl.SSLError:
                # Expected when the real client rejects a certificate during negotiation.
                pass
            except Exception as error:
                failures.append(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], commands
        finally:
            thread.join(timeout=6)
            assert not thread.is_alive(), "Local SMTP worker did not stop"
            assert not failures, failures


@pytest.mark.parametrize(
    ("server_name", "trust", "offer_tls", "accepted"),
    [
        ("valid", "trusted", True, True),
        ("valid", "other", True, False),
        ("wrong-host", "trusted", True, False),
        ("valid", "trusted", False, False),
    ],
)
def test_authentication_requires_verified_tls(
    certificates: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    server_name: str,
    trust: str,
    offer_tls: bool,
    accepted: bool,
) -> None:
    path = Path(__file__).resolve().parents[1] / "alert-mqtt-bridge" / "relay.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("smtp_bridge", path)
    assert spec is not None and spec.loader is not None
    bridge: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    monkeypatch.setenv("SSL_CERT_FILE", str(certificates / f"{trust}.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(certificates / "no-system-certificates"))
    bridge.SMTP_HOST = "localhost"
    bridge.SMTP_USER = "disposable-test-user"
    bridge.SMTP_PASS = "disposable-test-password"
    bridge.SMTP_FROM = "sender@example.invalid"
    bridge.SMTP_TO = ["recipient@example.invalid"]
    with smtp_server(certificates, server_name, offer_tls) as (port, commands):
        bridge.SMTP_PORT = port
        bridge.send_email("test", "warning", "Synthetic local alert", "test payload")
    assert (b"AUTH" in commands) is accepted
    assert (b"DATA" in commands) is accepted
    assert ("Email send failed" in caplog.text) is not accepted
    assert "disposable-test-password" not in caplog.text
