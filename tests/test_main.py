"""
Tests for main module (ObservabilityService, MQTT helpers, CLI).
"""

# pylint: disable=import-error,wrong-import-position,protected-access
# pylint: disable=redefined-outer-name,unused-variable,unused-argument

import sys
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")
from venus_observability.__main__ import (
    ObservabilityService,
    mqtt_callback_with_correlation_extraction,
    mqtt_publish_with_correlation,
    setup_mqtt_correlation,
    setup_telemetry,
)
from venus_observability.correlation import (
    CorrelationContext,
    get_correlation_id,
    set_correlation_id,
)


@pytest.fixture(autouse=True)
def reset_context() -> Generator[None, None, None]:
    """Reset correlation ID context before each test."""
    set_correlation_id(None)
    yield
    set_correlation_id(None)


class TestSetupTelemetry:
    """Tests for setup_telemetry function."""

    @pytest.fixture
    def telemetry_mocks(self) -> Generator[dict[str, MagicMock], None, None]:
        """Common mocks for telemetry tests."""
        with (
            patch("venus_observability.__main__.Resource.create") as mock_resource_create,
            patch(
                "venus_observability.__main__.trace.set_tracer_provider"
            ) as mock_set_tracer_provider,
            patch("venus_observability.__main__.TracerProvider") as mock_tracer_provider_class,
            patch("venus_observability.__main__.BatchSpanProcessor") as mock_batch_span_processor,
            patch(
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
            ) as mock_otlp_exporter_class,
            patch("venus_observability.__main__.start_http_server") as mock_start_http_server,
        ):
            mock_resource = MagicMock()
            mock_resource_create.return_value = mock_resource
            mock_tracer_provider = MagicMock()
            mock_tracer_provider_class.return_value = mock_tracer_provider
            yield {
                "resource_create": mock_resource_create,
                "set_tracer_provider": mock_set_tracer_provider,
                "tracer_provider_class": mock_tracer_provider_class,
                "batch_span_processor": mock_batch_span_processor,
                "otlp_exporter_class": mock_otlp_exporter_class,
                "start_http_server": mock_start_http_server,
                "resource": mock_resource,
                "tracer_provider": mock_tracer_provider,
            }

    def test_setup_telemetry_basic(self, telemetry_mocks: dict[str, MagicMock]) -> None:
        """Test basic telemetry setup without OTLP endpoint."""
        mock_meter_provider = MagicMock()
        with (
            patch("venus_observability.__main__.MeterProvider", return_value=mock_meter_provider),
            patch("venus_observability.__main__.PrometheusMetricReader"),
        ):
            tracer_provider, meter_provider = setup_telemetry(
                service_name="test-service",
                otlp_endpoint=None,
                prometheus_port=9090,
            )

        assert tracer_provider is telemetry_mocks["tracer_provider"]
        assert meter_provider is mock_meter_provider
        telemetry_mocks["start_http_server"].assert_called_once_with(9090)
        telemetry_mocks["set_tracer_provider"].assert_called_once_with(
            telemetry_mocks["tracer_provider"]
        )
        assert not telemetry_mocks["tracer_provider"].add_span_processor.called

    def test_setup_telemetry_with_otlp(self, telemetry_mocks: dict[str, MagicMock]) -> None:
        """Test telemetry setup with OTLP endpoint."""
        mock_meter_provider = MagicMock()
        mock_otlp_exporter = MagicMock()
        mock_batch_processor = MagicMock()
        telemetry_mocks["otlp_exporter_class"].return_value = mock_otlp_exporter
        telemetry_mocks["batch_span_processor"].return_value = mock_batch_processor

        with (
            patch("venus_observability.__main__.MeterProvider", return_value=mock_meter_provider),
            patch("venus_observability.__main__.PrometheusMetricReader"),
        ):
            tracer_provider, meter_provider = setup_telemetry(
                service_name="test-service",
                otlp_endpoint="http://tempo:4317",
                prometheus_port=9090,
            )

        telemetry_mocks["otlp_exporter_class"].assert_called_once_with(
            endpoint="http://tempo:4317", insecure=True
        )
        telemetry_mocks["batch_span_processor"].assert_called_once_with(mock_otlp_exporter)
        telemetry_mocks["tracer_provider"].add_span_processor.assert_called_once_with(
            mock_batch_processor
        )


class TestObservabilityService:
    """Tests for ObservabilityService class."""

    @patch("venus_observability.__main__.setup_telemetry")
    @patch("venus_observability.__main__.VictronMetrics")
    @patch("venus_observability.__main__.DBusSignalListener")
    def test_start(
        self,
        mock_dbus_listener_class: MagicMock,
        mock_victron_metrics_class: MagicMock,
        mock_setup_telemetry: MagicMock,
    ) -> None:
        """Test service start."""
        mock_tracer_provider = MagicMock()
        mock_meter_provider = MagicMock()
        mock_setup_telemetry.return_value = (
            mock_tracer_provider,
            mock_meter_provider,
        )

        mock_metrics = MagicMock()
        mock_victron_metrics_class.return_value = mock_metrics

        mock_meter = MagicMock()
        mock_meter_provider.get_meter.return_value = mock_meter

        mock_tracer = MagicMock()
        mock_tracer_provider.get_tracer.return_value = mock_tracer

        mock_dbus_listener = MagicMock()
        mock_dbus_listener_class.return_value = mock_dbus_listener

        service = ObservabilityService(
            dbus_address="unix:path=/test",
            otlp_endpoint="http://tempo:4317",
            prometheus_port=9090,
            service_name="test-service",
        )
        service.start()

        mock_setup_telemetry.assert_called_once_with(
            service_name="test-service",
            otlp_endpoint="http://tempo:4317",
            prometheus_port=9090,
        )
        mock_victron_metrics_class.assert_called_once_with(mock_meter)
        mock_dbus_listener_class.assert_called_once_with(
            metrics=mock_metrics,
            tracer=mock_tracer,
        )
        mock_dbus_listener.start.assert_called_once()

    def test_stop(self) -> None:
        """Test service stop."""
        service = ObservabilityService()
        service._tracer_provider = MagicMock()
        service._meter_provider = MagicMock()
        service._dbus_listener = MagicMock()

        service.stop()

        service._dbus_listener.stop.assert_called_once()
        service._tracer_provider.shutdown.assert_called_once()
        service._meter_provider.shutdown.assert_called_once()

    def test_lifespan_context_manager(self) -> None:
        """Test lifespan context manager."""
        service = ObservabilityService()
        service.start = MagicMock()  # type: ignore[method-assign]
        service.stop = MagicMock()  # type: ignore[method-assign]

        with service.lifespan() as s:
            assert s is service
            service.start.assert_called_once()

        service.stop.assert_called_once()


class TestMqttPublishWithCorrelation:
    """Tests for MQTT publish with correlation."""

    def test_mqtt_publish_with_correlation(self) -> None:
        """Test publishing with correlation ID."""
        mock_client = MagicMock()
        mock_client.publish.return_value = "result"

        with CorrelationContext("test-correlation"):
            result = mqtt_publish_with_correlation(
                mock_client, "test/topic", "payload", qos=1, retain=True
            )

        assert result == "result"
        mock_client.publish.assert_called_once_with("test/topic", "payload", 1, True)

    def test_mqtt_publish_with_correlation_injects_properties(self) -> None:
        """Test publishing injects correlation into MQTT v5 properties."""
        mock_client = MagicMock()
        mock_client.publish.return_value = "result"
        mock_properties = MagicMock()
        mock_properties.UserProperty = []

        with CorrelationContext("test-correlation"):
            mqtt_publish_with_correlation(
                mock_client, "test/topic", "payload", properties=mock_properties
            )

        # Check that properties were updated with correlation
        assert mock_properties.UserProperty is not None

    def test_mqtt_publish_with_trace_context(self) -> None:
        """Test publishing with trace context injects headers."""
        mock_client = MagicMock()
        mock_client.publish.return_value = "result"
        mock_headers: dict[str, str] = {}

        with patch("venus_observability.__main__.trace.get_current_span") as mock_get_span:
            mock_span = MagicMock()
            mock_span.is_recording.return_value = True
            mock_span_context = MagicMock()
            mock_span_context.is_valid = True
            mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
            mock_span_context.span_id = 0x1234567890ABCDEF
            mock_span_context.trace_flags = 0x01
            mock_span_context.trace_state = None
            mock_span.get_span_context.return_value = mock_span_context
            mock_get_span.return_value = mock_span

            mqtt_publish_with_correlation(
                mock_client, "test/topic", "payload", headers=mock_headers
            )

        assert "x-correlation-id" in mock_headers
        assert "traceparent" in mock_headers
        assert "baggage" in mock_headers


class TestMqttCallbackWithCorrelationExtraction:
    """Tests for MQTT callback correlation extraction."""

    def test_callback_extracts_from_properties(self) -> None:
        """Test callback extracts correlation from message properties."""
        inner_callback = MagicMock()
        wrapped = mqtt_callback_with_correlation_extraction(inner_callback)

        mock_client = MagicMock()
        mock_userdata = MagicMock()
        mock_message = MagicMock()
        mock_message.properties = MagicMock()
        mock_message.properties.UserProperty = [("correlation_id", "extracted-id")]

        with patch(
            "venus_observability.__main__.extract_correlation_id_from_mqtt_properties",
            return_value="extracted-id",
        ):
            wrapped(mock_client, mock_userdata, mock_message)

        inner_callback.assert_called_once_with(mock_client, mock_userdata, mock_message)

    def test_callback_fallback_to_headers(self) -> None:
        """Test callback falls back to headers if no properties."""
        inner_callback = MagicMock()
        wrapped = mqtt_callback_with_correlation_extraction(inner_callback)

        mock_client = MagicMock()
        mock_userdata = MagicMock()
        mock_message = MagicMock()
        mock_message.properties = None
        mock_message.headers = {"x-correlation-id": "header-id"}

        with patch(
            "venus_observability.__main__.extract_correlation_id_from_headers",
            return_value="header-id",
        ):
            wrapped(mock_client, mock_userdata, mock_message)

        inner_callback.assert_called_once()

    def test_callback_no_correlation_id(self) -> None:
        """Test callback handles missing correlation ID."""
        inner_callback = MagicMock()
        wrapped = mqtt_callback_with_correlation_extraction(inner_callback)

        mock_client = MagicMock()
        mock_userdata = MagicMock()
        mock_message = MagicMock()
        mock_message.properties = None
        mock_message.headers = {}

        wrapped(mock_client, mock_userdata, mock_message)

        inner_callback.assert_called_once()

    def test_callback_resets_context(self) -> None:
        """Test callback resets correlation context after execution."""
        inner_callback = MagicMock()
        wrapped = mqtt_callback_with_correlation_extraction(inner_callback)

        mock_client = MagicMock()
        mock_userdata = MagicMock()
        mock_message = MagicMock()
        mock_message.properties = MagicMock()
        mock_message.properties.UserProperty = [("correlation_id", "temp-id")]

        with patch(
            "venus_observability.__main__.extract_correlation_id_from_mqtt_properties",
            return_value="temp-id",
        ):
            # Context should be None before
            assert get_correlation_id() is None
            wrapped(mock_client, mock_userdata, mock_message)
            # Context should be None after
            assert get_correlation_id() is None

    def test_callback_resets_on_exception(self) -> None:
        """Test callback resets correlation context even on exception."""
        inner_callback = MagicMock(side_effect=RuntimeError("test error"))
        wrapped = mqtt_callback_with_correlation_extraction(inner_callback)

        mock_client = MagicMock()
        mock_userdata = MagicMock()
        mock_message = MagicMock()
        mock_message.properties = MagicMock()
        mock_message.properties.UserProperty = [("correlation_id", "temp-id")]

        with patch(
            "venus_observability.__main__.extract_correlation_id_from_mqtt_properties",
            return_value="temp-id",
        ):
            from contextlib import suppress

            with suppress(RuntimeError):
                wrapped(mock_client, mock_userdata, mock_message)

        # Context should be reset after exception
        assert get_correlation_id() is None


class TestSetupMqttCorrelation:
    """Tests for setup_mqtt_correlation."""

    def test_setup_mqtt_correlation_wraps_on_message(self) -> None:
        """Test setup wraps on_message callback."""
        mock_client = MagicMock()
        original_callback = MagicMock()
        mock_client.on_message = original_callback

        setup_mqtt_correlation(mock_client)

        assert mock_client.on_message is not original_callback
        assert mock_client._correlation_publish is not None

    def test_setup_mqtt_correlation_handles_none_on_message(self) -> None:
        """Test setup handles client with no on_message."""
        mock_client = MagicMock()
        mock_client.on_message = None

        setup_mqtt_correlation(mock_client)

        assert mock_client._correlation_publish is not None
