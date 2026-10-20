"""
Core observability module for Venus OS.

Provides OpenTelemetry initialization, D-Bus signal tracing, and Prometheus metrics export.
"""

import logging
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from threading import Lock, Thread
from types import FrameType
from typing import Any
from wsgiref.simple_server import WSGIServer

from opentelemetry import trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from prometheus_client import start_http_server

from .correlation import (
    CorrelationIDPropagator,
    extract_correlation_id_from_headers,
    extract_correlation_id_from_mqtt_properties,
    get_correlation_id,
    inject_correlation_id_into_headers,
    inject_correlation_id_into_mqtt_properties,
    set_correlation_id,
)
from .dbus_listener import DBusSignalListener
from .metrics import VictronMetrics

logger = logging.getLogger(__name__)

# Global correlation propagator
_correlation_propagator: CorrelationIDPropagator | None = None


class _PrometheusHTTPReader(PrometheusMetricReader):
    """Keep the HTTP listener under the meter provider's shutdown ownership."""

    def __init__(self) -> None:
        self._http_server: tuple[WSGIServer, Thread] | None = None
        self._http_lock = Lock()
        self._http_closed = False
        super().__init__()

    def start_http_server(self, port: int) -> None:
        with self._http_lock:
            if self._http_closed or self._http_server is not None:
                raise RuntimeError("Prometheus HTTP reader is already started or closed")
            self._http_server = start_http_server(port)

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        with self._http_lock:
            if self._http_closed:
                return
            self._http_closed = True
            handle, self._http_server = self._http_server, None
            # Every closer runs even if an earlier one raises. Stop accepting
            # scrapes before unregistering the reader from the global registry.
            with ExitStack() as cleanup:
                cleanup.callback(super().shutdown, timeout_millis=timeout_millis, **kwargs)
                if handle is not None:
                    server, thread = handle
                    cleanup.callback(thread.join, max(0, timeout_millis) / 1000)
                    cleanup.callback(server.server_close)
                    server.shutdown()
            if handle is not None and handle[1].is_alive():
                raise TimeoutError("Prometheus HTTP serving thread did not stop")


def setup_telemetry(
    service_name: str = "venus-os-observability",
    otlp_endpoint: str | None = None,
    prometheus_port: int = 9090,
) -> tuple[TracerProvider, MeterProvider]:
    """Initialize OpenTelemetry tracing and metrics.

    Args:
        service_name: Service name for traces/metrics
        otlp_endpoint: OTLP endpoint. Protocol is selected explicitly through the OTLP
            environment variables; defaults to local plaintext gRPC. None skips export.
        prometheus_port: Port for Prometheus metrics HTTP server

    Returns:
        Tuple of (TracerProvider, MeterProvider). Shut down both providers when
        finished; MeterProvider.shutdown() also closes the Prometheus listener.
    """
    global _correlation_propagator

    rollback = ExitStack()
    try:
        # Transfer cleanup to each containing SDK object only after it accepts
        # ownership, so failures during construction also release resources.
        exporter_owner = ExitStack()
        rollback.callback(exporter_owner.close)
        otlp_exporter = None
        if otlp_endpoint:
            from .otlp import create_exporter

            # Reject invalid transport configuration before global registration.
            otlp_exporter = create_exporter(otlp_endpoint)
            exporter_owner.callback(otlp_exporter.shutdown)

        resource = Resource.create({SERVICE_NAME: service_name})
        tracer_provider = TracerProvider(
            resource=resource, sampler=None if otlp_endpoint else ALWAYS_OFF
        )
        rollback.callback(tracer_provider.shutdown)
        if otlp_exporter is not None:
            processor = BatchSpanProcessor(otlp_exporter)
            exporter_owner.pop_all()
            exporter_owner.callback(processor.shutdown)
            tracer_provider.add_span_processor(processor)
            exporter_owner.pop_all()
            logger.info("OTLP trace exporter configured")

        prometheus_reader = _PrometheusHTTPReader()
        reader_owner = ExitStack()
        rollback.callback(reader_owner.close)
        reader_owner.callback(prometheus_reader.shutdown)
        meter_provider = MeterProvider(resource=resource, metric_readers=[prometheus_reader])
        reader_owner.pop_all()
        rollback.callback(meter_provider.shutdown)
        prometheus_reader.start_http_server(prometheus_port)
        logger.info("Prometheus metrics server started on port %d", prometheus_port)

        # Install global hooks only after locally owned resources are ready.
        propagator = CorrelationIDPropagator()
        set_global_textmap(propagator)
        _correlation_propagator = propagator
        trace.set_tracer_provider(tracer_provider)
    except BaseException:
        try:
            rollback.close()
        except BaseException:
            logger.exception("Telemetry rollback failed")
        raise
    rollback.pop_all()

    return tracer_provider, meter_provider


class ObservabilityService:
    """Main observability service coordinating D-Bus listener, metrics, and tracing."""

    def __init__(
        self,
        dbus_address: str = "unix:path=/var/run/dbus/system_bus_socket",
        otlp_endpoint: str | None = None,
        prometheus_port: int = 9090,
        service_name: str = "venus-os-observability",
    ):
        self.dbus_address = dbus_address
        self.otlp_endpoint = otlp_endpoint
        self.prometheus_port = prometheus_port
        self.service_name = service_name

        self._tracer_provider: TracerProvider | None = None
        self._meter_provider: MeterProvider | None = None
        self._dbus_listener: DBusSignalListener | None = None
        self._metrics: VictronMetrics | None = None

    def start(self) -> None:
        """Start all observability components."""
        if any(
            component is not None
            for component in (self._tracer_provider, self._meter_provider, self._dbus_listener)
        ):
            raise RuntimeError("Observability service is already started")
        logger.info("Starting %s observability service", self.service_name)

        try:
            self._tracer_provider, self._meter_provider = setup_telemetry(
                service_name=self.service_name,
                otlp_endpoint=self.otlp_endpoint,
                prometheus_port=self.prometheus_port,
            )
            self._metrics = VictronMetrics(self._meter_provider.get_meter(self.service_name))
            self._dbus_listener = DBusSignalListener(
                metrics=self._metrics,
                tracer=self._tracer_provider.get_tracer(self.service_name),
            )

            # ItemsChanged signals are emitted at / with absolute item paths.
            self._dbus_listener.subscribe_global()
            self._dbus_listener.start()
        except BaseException:
            try:
                self.stop()
            except BaseException:
                logger.exception("Observability service rollback failed")
            raise
        logger.info("Observability service started")

    def stop(self) -> None:
        """Stop all observability components."""
        logger.info("Stopping observability service")
        listener, self._dbus_listener = self._dbus_listener, None
        tracer, self._tracer_provider = self._tracer_provider, None
        meter, self._meter_provider = self._meter_provider, None
        self._metrics = None
        with ExitStack() as cleanup:
            if meter is not None:
                cleanup.callback(meter.shutdown)
            if tracer is not None:
                cleanup.callback(tracer.shutdown)
            if listener is not None:
                cleanup.callback(listener.stop)
        logger.info("Observability service stopped")

    @contextmanager
    def lifespan(self) -> Iterator["ObservabilityService"]:
        """Context manager for service lifecycle."""
        self.start()
        try:
            yield self
        finally:
            self.stop()


# MQTT correlation ID middleware functions
def mqtt_publish_with_correlation(
    client: Any, topic: str, payload: Any, qos: int = 0, retain: bool = False, **kwargs: Any
) -> Any:
    """
    Publish MQTT message with correlation ID propagation.
    Usage: mqtt_publish_with_correlation(client, "topic", "payload")
    """
    from opentelemetry import trace

    # Get or create correlation ID
    correlation_id = get_correlation_id()

    # Get current trace context for W3C propagation
    span = trace.get_current_span()
    trace_context = None
    if span and span.is_recording():
        span_context = span.get_span_context()
        if span_context and span_context.is_valid:
            trace_context = {
                "trace_id": format(span_context.trace_id, "032x"),
                "span_id": format(span_context.span_id, "016x"),
                "trace_flags": format(span_context.trace_flags, "02x"),
            }

    # Inject correlation ID into MQTT properties if using MQTT v5
    properties = kwargs.get("properties")
    if properties:
        inject_correlation_id_into_mqtt_properties(properties, correlation_id)

    # Also inject into headers if provided
    headers = kwargs.get("headers")
    if headers is not None:
        inject_correlation_id_into_headers(headers, correlation_id, trace_context)

    return client.publish(topic, payload, qos, retain, **kwargs)


def mqtt_callback_with_correlation_extraction(callback: Any) -> Any:
    """
    Wrapper for MQTT callback to extract correlation ID from incoming messages.
    Usage: client.on_message = mqtt_callback_with_correlation_extraction(my_callback)
    """

    def wrapped_callback(client: Any, userdata: Any, message: Any) -> None:
        # Extract correlation ID from message properties (MQTT v5)
        correlation_id = None
        if hasattr(message, "properties") and message.properties:
            correlation_id = extract_correlation_id_from_mqtt_properties(message.properties)

        # Fallback: check headers if available
        if not correlation_id and hasattr(message, "headers") and message.headers:
            correlation_id = extract_correlation_id_from_headers(message.headers)

        # Set correlation ID in context for this callback
        token = None
        if correlation_id:
            token = set_correlation_id(correlation_id)

        try:
            callback(client, userdata, message)
        finally:
            if token:
                # Reset context after callback
                from .correlation import reset_correlation_id

                reset_correlation_id(token)

    return wrapped_callback


def setup_mqtt_correlation(client: Any) -> None:
    """Configure MQTT client for correlation ID propagation."""
    # Wrap on_message to extract correlation ID
    original_on_message = client.on_message
    if original_on_message:
        client.on_message = mqtt_callback_with_correlation_extraction(original_on_message)

    # Store reference for publish operations
    client._correlation_publish = mqtt_publish_with_correlation


# Backward compatibility - keep the main function
def main() -> None:
    """CLI entry point."""
    import signal
    import sys

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dbus_addr = os.getenv("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/var/run/dbus/system_bus_socket")
    service = ObservabilityService(
        dbus_address=dbus_addr,
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
        prometheus_port=int(os.getenv("PROMETHEUS_PORT", "9090")),
        service_name=os.getenv("OTEL_SERVICE_NAME", "venus-os-observability"),
    )

    def signal_handler(signum: int, frame: FrameType | None) -> None:
        logger.info("Received signal %s, shutting down", signum)
        # The lifespan context owns cleanup, including signal-triggered exits.
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    with service.lifespan():
        # Keep running
        import time

        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
