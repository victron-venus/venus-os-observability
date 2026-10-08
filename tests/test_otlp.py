"""Configuration and lifecycle boundaries of the actual optional OTLP exporters."""

import os
import subprocess
import sys
import traceback
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
import requests

from venus_observability.otlp import _HTTPSOnlySession, create_exporter


@pytest.fixture(autouse=True)
def otlp_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in os.environ:
        if name.startswith("OTEL_EXPORTER_OTLP"):
            monkeypatch.delenv(name)
    yield


def test_disabled_export_does_not_import_optional_transports() -> None:
    script = """
import sys
from unittest.mock import MagicMock, patch
for name in ('dbus', 'dbus.mainloop', 'dbus.mainloop.glib', 'gi', 'gi.repository'):
    sys.modules[name] = MagicMock()
from venus_observability.__main__ import setup_telemetry
with patch('venus_observability.__main__.start_http_server'):
    trace, meter = setup_telemetry()
assert 'venus_observability.otlp' not in sys.modules
assert 'opentelemetry.exporter.otlp.proto.grpc.trace_exporter' not in sys.modules
assert 'opentelemetry.exporter.otlp.proto.http.trace_exporter' not in sys.modules
trace.shutdown()
meter.shutdown()
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)


@pytest.mark.parametrize("protocol", ["grpc", "unknown"])
def test_invalid_secure_transport_fails_before_global_registration(
    monkeypatch: pytest.MonkeyPatch, protocol: str
) -> None:
    from venus_observability.__main__ import setup_telemetry

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", protocol)
    with (
        patch("venus_observability.__main__.TracerProvider") as provider,
        pytest.raises(ValueError, match="OTLP|HTTPS gRPC"),
    ):
        setup_telemetry(otlp_endpoint="https://collector.invalid:4317")
    provider.assert_not_called()


def test_existing_plaintext_grpc_is_preserved() -> None:
    with patch(
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
    ) as exporter:
        create_exporter("http://tempo:4317")
    exporter.assert_called_once_with(endpoint="http://tempo:4317", insecure=True)


@pytest.mark.parametrize(
    ("traces", "generic", "expected"),
    [
        ("", "http/protobuf", "http/protobuf"),
        (None, "", "grpc"),
        ("", "", "grpc"),
        (" http/protobuf ", "grpc", "http/protobuf"),
    ],
)
def test_protocol_empty_values_fall_back(
    monkeypatch: pytest.MonkeyPatch, traces: str | None, generic: str, expected: str
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", generic)
    if traces is not None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", traces)
    if expected == "grpc":
        with patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as exporter:
            create_exporter("http://tempo:4317")
        exporter.assert_called_once_with(endpoint="http://tempo:4317", insecure=True)
    else:
        http_exporter: Any = create_exporter("https://localhost:4318")
        try:
            assert http_exporter._endpoint == "https://localhost:4318/v1/traces"
        finally:
            http_exporter.shutdown()


@pytest.mark.parametrize(
    "endpoint",
    ["http://localhost:4318", "https:///", "https://user:password@localhost", "https://x/#y"],
)
def test_http_requires_unambiguous_https_endpoint(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    with pytest.raises(ValueError, match="requires an HTTPS endpoint"):
        create_exporter(endpoint)


def test_standard_http_configuration_and_trace_specific_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://localhost/explicit")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "common=ignored")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "fixture=trace-specific")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_CERTIFICATE", "common-ca.pem")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE", "trace-ca.pem")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_CLIENT_KEY", "client.key")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_CLIENT_CERTIFICATE", "client.pem")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "7")
    exporter: Any = create_exporter("https://localhost/base")
    try:
        assert exporter._endpoint == "https://localhost/explicit"
        assert exporter._headers == {"fixture": "trace-specific"}
        assert exporter._certificate_file == "trace-ca.pem"
        assert exporter._client_cert == ("client.pem", "client.key")
        assert exporter._timeout == 7
    finally:
        exporter.shutdown()


def test_generic_http_endpoint_appends_trace_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    exporter: Any = create_exporter("https://localhost:4318/base/?tenant=synthetic")
    try:
        assert exporter._endpoint == "https://localhost:4318/base/v1/traces?tenant=synthetic"
    finally:
        exporter.shutdown()


def test_constructor_failure_closes_owned_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    with (
        patch("venus_observability.otlp._HTTPSOnlySession") as session,
        patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            side_effect=RuntimeError("configuration rejected"),
        ),
        pytest.raises(RuntimeError, match="configuration rejected"),
    ):
        create_exporter("https://localhost")
    session.return_value.close.assert_called_once()


def test_transport_diagnostics_do_not_expose_query_credentials() -> None:
    endpoint = "https://localhost/trace?token=synthetic-private-query"
    failure = requests.exceptions.SSLError(f"Could not connect to {endpoint}")
    with (
        _HTTPSOnlySession() as session,
        patch("requests.Session.send", side_effect=failure),
        pytest.raises(requests.exceptions.SSLError) as caught,
    ):
        session.post(endpoint, data=b"synthetic-span")
    formatted = "".join(traceback.format_exception(caught.value))
    assert "synthetic-private-query" not in formatted
    assert "OTLP HTTPS transport failed" in formatted
