"""Real loopback checks for ownership of the Prometheus listener."""

import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.client import HTTPConnection
from threading import Thread
from typing import Any
from unittest.mock import MagicMock
from wsgiref.simple_server import WSGIServer

import pytest
from opentelemetry import trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry, start_http_server

import venus_observability.__main__ as main


@dataclass
class HTTPHarness:
    """Own test-only sockets even when a baseline regression leaks them."""

    listeners: list[tuple[WSGIServer, Thread]] = field(default_factory=list)
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    dbus: MagicMock = field(default_factory=MagicMock)

    def start(self, port: int) -> tuple[WSGIServer, Thread]:
        assert port == 0
        listener = start_http_server(port, addr="127.0.0.1", registry=self.registry)
        self.listeners.append(listener)
        return listener

    def assert_released(self) -> None:
        assert self.listeners, "test must actually start a listener"
        for server, thread in self.listeners:
            assert not thread.is_alive(), "Prometheus serving thread was leaked"
            assert server.socket.fileno() == -1, "Prometheus listening socket was leaked"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as replacement:
                replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                replacement.bind(server.server_address)
                replacement.listen(1)


@pytest.fixture
def http_harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[HTTPHarness]:
    harness = HTTPHarness()
    monkeypatch.setattr(main, "start_http_server", harness.start)
    original_init = PrometheusMetricReader.__init__

    def isolated_init(reader: Any) -> None:
        original_init(reader, registry=harness.registry)

    monkeypatch.setattr(PrometheusMetricReader, "__init__", isolated_init)
    monkeypatch.setattr(trace, "set_tracer_provider", lambda _: None)
    monkeypatch.setattr(main, "set_global_textmap", lambda _: None)
    monkeypatch.setattr(main, "DBusSignalListener", lambda **_: harness.dbus)
    try:
        yield harness
    finally:
        for server, thread in harness.listeners:
            if thread.is_alive():
                bounded_call(server.shutdown)
            server.server_close()
            thread.join(timeout=2)
            assert not thread.is_alive(), "test fixture could not close its serving thread"


def bounded_call(action: Callable[[], Any]) -> None:
    """Fail promptly instead of letting a shutdown regression hang the suite."""
    errors: list[BaseException] = []

    def invoke() -> None:
        try:
            action()
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=invoke, daemon=True)
    thread.start()
    thread.join(timeout=3)
    assert not thread.is_alive(), "lifecycle call did not return within three seconds"
    if errors:
        raise errors[0]


def assert_http_serves(harness: HTTPHarness) -> None:
    server, thread = harness.listeners[-1]
    assert thread.is_alive()
    host = server.server_address[0]
    assert isinstance(host, str)
    connection = HTTPConnection(host, server.server_address[1], timeout=2)
    try:
        connection.request("GET", "/metrics")
        response = connection.getresponse()
        assert response.status == 200
        response.read()
    finally:
        connection.close()


def test_service_stop_releases_real_http_listener(http_harness: HTTPHarness) -> None:
    service = main.ObservabilityService(prometheus_port=0)
    try:
        service.start()
        assert_http_serves(http_harness)
        bounded_call(service.stop)
        http_harness.assert_released()
        bounded_call(service.stop)
        http_harness.dbus.stop.assert_called_once()
    finally:
        bounded_call(service.stop)


def test_direct_setup_keeps_two_provider_api_and_meter_owns_http(
    http_harness: HTTPHarness,
) -> None:
    providers = main.setup_telemetry(prometheus_port=0)
    assert isinstance(providers, tuple)
    assert len(providers) == 2
    tracer, meter = providers
    try:
        assert_http_serves(http_harness)
        bounded_call(meter.shutdown)
        http_harness.assert_released()
    finally:
        bounded_call(tracer.shutdown)
        bounded_call(meter.shutdown)


@pytest.mark.parametrize("stage", ["metrics", "dbus_start"])
def test_partial_service_start_releases_real_http_listener(
    http_harness: HTTPHarness, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    failure = RuntimeError("synthetic startup failure")
    if stage == "metrics":
        monkeypatch.setattr(main, "VictronMetrics", MagicMock(side_effect=failure))
    else:
        http_harness.dbus.start.side_effect = failure
    service = main.ObservabilityService(prometheus_port=0)
    try:
        with pytest.raises(RuntimeError) as caught, service.lifespan():
            pytest.fail("failed startup must not enter the lifespan")
        assert caught.value is failure
        http_harness.assert_released()
    finally:
        bounded_call(service.stop)


def test_setup_bind_failure_releases_providers_and_registered_reader(
    http_harness: HTTPHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdowns: list[MagicMock] = []

    def create_tracer(**kwargs: Any) -> Any:
        provider = TracerProvider(**kwargs)
        shutdown = MagicMock(wraps=provider.shutdown)
        shutdowns.append(shutdown)
        monkeypatch.setattr(provider, "shutdown", shutdown)
        return provider

    register = MagicMock(wraps=http_harness.registry.register)
    unregister = MagicMock(wraps=http_harness.registry.unregister)
    monkeypatch.setattr(http_harness.registry, "register", register)
    monkeypatch.setattr(http_harness.registry, "unregister", unregister)
    monkeypatch.setattr(main, "TracerProvider", create_tracer)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)

        def fail_to_bind(_port: int) -> tuple[WSGIServer, Thread]:
            return start_http_server(
                occupied.getsockname()[1], addr="127.0.0.1", registry=http_harness.registry
            )

        monkeypatch.setattr(main, "start_http_server", fail_to_bind)
        try:
            with pytest.raises(OSError):
                main.setup_telemetry(prometheus_port=0)
            assert len(shutdowns) == 1
            shutdowns[0].assert_called_once()
            register.assert_called_once()
            unregister.assert_called_once_with(register.call_args.args[0])
        finally:
            for shutdown in shutdowns:
                if not shutdown.called:
                    shutdown()


def test_setup_failure_after_bind_releases_http_listener(
    http_harness: HTTPHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("synthetic propagator initialization failure")
    monkeypatch.setattr(main, "set_global_textmap", MagicMock(side_effect=failure))
    with pytest.raises(RuntimeError) as caught:
        main.setup_telemetry(prometheus_port=0)
    assert caught.value is failure
    http_harness.assert_released()


@pytest.mark.parametrize("component", ["dbus", "tracer"])
def test_stop_error_does_not_skip_http_cleanup(
    http_harness: HTTPHarness, monkeypatch: pytest.MonkeyPatch, component: str
) -> None:
    failure = RuntimeError("synthetic component stop failure")
    service = main.ObservabilityService(prometheus_port=0)
    service.start()
    assert service._tracer_provider is not None
    original_shutdown = service._tracer_provider.shutdown

    def shutdown_tracer() -> None:
        original_shutdown()
        if component == "tracer":
            raise failure

    shutdown = MagicMock(side_effect=shutdown_tracer)
    monkeypatch.setattr(service._tracer_provider, "shutdown", shutdown)
    if component == "dbus":
        http_harness.dbus.stop.side_effect = failure
    try:
        with pytest.raises(RuntimeError) as caught:
            bounded_call(service.stop)
        assert caught.value is failure
        http_harness.assert_released()
        bounded_call(service.stop)
        http_harness.dbus.stop.assert_called_once()
        shutdown.assert_called_once()
    finally:
        http_harness.dbus.stop.side_effect = None
        if not shutdown.called:
            original_shutdown()
        bounded_call(service.stop)


def test_start_error_preserved_when_cleanup_also_fails(http_harness: HTTPHarness) -> None:
    failure = KeyboardInterrupt("synthetic interrupted startup")
    http_harness.dbus.start.side_effect = failure
    http_harness.dbus.stop.side_effect = RuntimeError("synthetic cleanup failure")
    service = main.ObservabilityService(prometheus_port=0)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            service.start()
        assert caught.value is failure
        http_harness.assert_released()
    finally:
        http_harness.dbus.stop.side_effect = None
        bounded_call(service.stop)


def test_duplicate_start_keeps_original_listener_owned(http_harness: HTTPHarness) -> None:
    service = main.ObservabilityService(prometheus_port=0)
    try:
        service.start()
        with pytest.raises(RuntimeError, match="already"):
            service.start()
        assert len(http_harness.listeners) == 1
        assert_http_serves(http_harness)
        bounded_call(service.stop)
        http_harness.assert_released()
    finally:
        bounded_call(service.stop)
