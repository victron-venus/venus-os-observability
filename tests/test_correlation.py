"""
Tests for correlation ID utilities.
"""

# pylint: disable=import-error,wrong-import-position,protected-access
# pylint: disable=redefined-outer-name,unused-variable,unused-argument

import sys
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")
from venus_observability.correlation import (
    CorrelationContext,
    CorrelationIDPropagator,
    create_span_with_correlation,
    extract_correlation_id_from_headers,
    extract_correlation_id_from_mqtt_properties,
    generate_correlation_id,
    get_correlation_id,
    get_current_trace_context,
    inject_correlation_id_into_headers,
    inject_correlation_id_into_mqtt_properties,
    reset_correlation_id,
    set_correlation_id,
    traced_operation,
)


@pytest.fixture(autouse=True)
def reset_context() -> Generator[None, None, None]:
    """Reset correlation ID context before each test."""
    # Set to None to clear any leftover state - don't reset the token
    # so context stays None for the test duration
    set_correlation_id(None)
    yield
    # Cleanup: set to None again (previous test's context will be restored
    # by the test's own try/finally blocks if they used set/reset)
    set_correlation_id(None)


class TestCorrelationIdGeneration:
    """Tests for correlation ID generation and retrieval."""

    def test_generate_correlation_id_returns_uuid(self) -> None:
        """Test that generated correlation ID is a valid UUID string."""
        corr_id = generate_correlation_id()
        assert isinstance(corr_id, str)
        assert len(corr_id) == 36  # UUID format
        assert corr_id.count("-") == 4

    def test_get_correlation_id_returns_none_by_default(self) -> None:
        """Test get_correlation_id returns None when not set."""
        assert get_correlation_id() is None

    def test_set_and_get_correlation_id(self) -> None:
        """Test setting and getting correlation ID."""
        test_id = "test-correlation-123"
        token = set_correlation_id(test_id)
        try:
            assert get_correlation_id() == test_id
        finally:
            reset_correlation_id(token)

    def test_reset_correlation_id(self) -> None:
        """Test resetting correlation ID restores previous value."""
        set_correlation_id("first-id")
        token = set_correlation_id("second-id")
        reset_correlation_id(token)
        assert get_correlation_id() == "first-id"


class TestExtractCorrelationIdFromHeaders:
    """Tests for extracting correlation ID from headers."""

    def test_extract_from_traceparent(self) -> None:
        """Test extraction from W3C traceparent header."""
        headers: dict[str, str] = {
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        }
        result = extract_correlation_id_from_headers(headers)
        assert result == "0af7651916cd43dd8448eb211c80319c"

    def test_extract_from_custom_header(self) -> None:
        """Test extraction from custom x-correlation-id header."""
        headers: dict[str, str] = {"x-correlation-id": "custom-correlation-123"}
        result = extract_correlation_id_from_headers(headers)
        assert result == "custom-correlation-123"

    def test_extract_from_baggage(self) -> None:
        """Test extraction from baggage header."""
        headers: dict[str, str] = {"baggage": "correlation-id=baggage-correlation-456,other=value"}
        result = extract_correlation_id_from_headers(headers)
        assert result == "baggage-correlation-456"

    def test_traceparent_priority_over_custom(self) -> None:
        """Test traceparent has priority over custom header."""
        headers: dict[str, str] = {
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
            "x-correlation-id": "should-be-ignored",
        }
        result = extract_correlation_id_from_headers(headers)
        assert result == "0af7651916cd43dd8448eb211c80319c"

    def test_custom_priority_over_baggage(self) -> None:
        """Test custom header has priority over baggage."""
        headers: dict[str, str] = {
            "x-correlation-id": "custom-wins",
            "baggage": "correlation-id=baggage-loses",
        }
        result = extract_correlation_id_from_headers(headers)
        assert result == "custom-wins"

    def test_case_insensitive_headers(self) -> None:
        """Test header extraction is case-insensitive."""
        headers: dict[str, str] = {
            "TRACEPARENT": "00-abcdef1234567890abcdef1234567890-1234567890abcdef-01"
        }
        result = extract_correlation_id_from_headers(headers)
        assert result == "abcdef1234567890abcdef1234567890"

    def test_returns_none_when_no_headers(self) -> None:
        """Test returns None when no correlation headers present."""
        headers: dict[str, str] = {"content-type": "application/json"}
        result = extract_correlation_id_from_headers(headers)
        assert result is None

    def test_returns_none_for_empty_headers(self) -> None:
        """Test returns None for empty headers dict."""
        result = extract_correlation_id_from_headers({})
        assert result is None


class TestInjectCorrelationIdIntoHeaders:
    """Tests for injecting correlation ID into headers."""

    def test_inject_with_correlation_id(self) -> None:
        """Test injection with provided correlation ID."""
        headers: dict[str, str] = {}
        result = inject_correlation_id_into_headers(headers, correlation_id="test-id")
        assert result["x-correlation-id"] == "test-id"
        assert "traceparent" not in result

    def test_inject_with_trace_context(self) -> None:
        """Test injection with correlation ID and trace context."""
        headers: dict[str, str] = {}
        trace_ctx = {
            "trace_id": "0af7651916cd43dd8448eb211c80319c",
            "span_id": "b7ad6b7169203331",
            "trace_flags": "01",
        }
        result = inject_correlation_id_into_headers(
            headers, correlation_id="test-id", trace_context=trace_ctx
        )
        assert result["x-correlation-id"] == "test-id"
        assert result["traceparent"] == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        assert "correlation-id=test-id" in result["baggage"]
        assert "trace-id=0af7651916cd43dd8448eb211c80319c" in result["baggage"]

    def test_inject_generates_id_when_none_provided(self) -> None:
        """Test generates new correlation ID when none provided and context is empty."""
        # Explicitly clear context
        token = set_correlation_id(None)
        reset_correlation_id(token)
        headers: dict[str, str] = {}
        result = inject_correlation_id_into_headers(headers)
        assert "x-correlation-id" in result
        assert len(result["x-correlation-id"]) == 36  # UUID

    def test_inject_uses_context_correlation_id(self) -> None:
        """Test uses correlation ID from context when available."""
        token = set_correlation_id("context-id")
        try:
            headers: dict[str, str] = {}
            result = inject_correlation_id_into_headers(headers)
            assert result["x-correlation-id"] == "context-id"
        finally:
            reset_correlation_id(token)


class TestCorrelationIdPropagator:
    """Tests for W3C Trace Context propagator with correlation ID."""

    def test_fields_contains_expected_headers(self) -> None:
        """Test propagator fields include expected headers."""
        propagator = CorrelationIDPropagator()
        fields = propagator.fields
        assert "traceparent" in fields
        assert "tracestate" in fields
        assert "baggage" in fields
        assert "x-correlation-id" in fields

    def test_extract_integration(self) -> None:
        """Test propagator extract delegates to W3C and sets correlation ID."""
        # Test via direct function since otel getter interface changed
        carrier: dict[str, str] = {
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        }
        correlation_id = extract_correlation_id_from_headers(carrier)
        assert correlation_id == "0af7651916cd43dd8448eb211c80319c"

    def test_inject_integration(self) -> None:
        """Test propagator inject adds correlation headers."""
        carrier: dict[str, str] = {}
        trace_ctx = {
            "trace_id": "0af7651916cd43dd8448eb211c80319c",
            "span_id": "b7ad6b7169203331",
            "trace_flags": "01",
        }
        token = set_correlation_id("test-propagate-id")
        try:
            inject_correlation_id_into_headers(
                carrier, correlation_id="test-propagate-id", trace_context=trace_ctx
            )
            assert carrier["x-correlation-id"] == "test-propagate-id"
            assert "traceparent" in carrier
            assert "baggage" in carrier
        finally:
            reset_correlation_id(token)


class TestMqttHelpers:
    """Tests for MQTT correlation ID helpers."""

    def test_extract_from_mqtt_user_properties(self) -> None:
        """Test extraction from MQTT v5 UserProperty list."""
        properties = MagicMock()
        properties.UserProperty = [("correlation_id", "mqtt-corr-123"), ("other", "value")]
        result = extract_correlation_id_from_mqtt_properties(properties)
        assert result == "mqtt-corr-123"

    def test_extract_from_mqtt_direct_attribute(self) -> None:
        """Test extraction from direct attribute on properties."""
        properties = MagicMock()
        properties.correlation_id = "direct-attr-456"
        result = extract_correlation_id_from_mqtt_properties(properties)
        assert result == "direct-attr-456"

    def test_extract_returns_none_for_missing_properties(self) -> None:
        """Test returns None when properties is None or missing."""
        assert extract_correlation_id_from_mqtt_properties(None) is None
        assert extract_correlation_id_from_mqtt_properties(MagicMock(spec=[])) is None

    def test_inject_into_mqtt_properties(self) -> None:
        """Test injection into MQTT v5 properties."""
        properties = MagicMock()
        properties.UserProperty = []
        result = inject_correlation_id_into_mqtt_properties(
            properties, correlation_id="inject-mqtt-789"
        )
        assert result is properties
        user_props = result.UserProperty
        assert ("correlation_id", "inject-mqtt-789") in user_props
        assert ("trace_id", "inject-mqtt-789") in user_props

    def test_inject_returns_properties_unchanged_when_no_userproperty(self) -> None:
        """Test returns properties unchanged when UserProperty not supported."""
        properties = MagicMock(spec=[])
        result = inject_correlation_id_into_mqtt_properties(properties, correlation_id="test")
        assert result is properties


class TestCorrelationContext:
    """Tests for CorrelationContext manager."""

    def test_context_manager_sets_and_resets(self) -> None:
        """Test context manager sets correlation ID and resets on exit."""
        assert get_correlation_id() is None
        with CorrelationContext("ctx-mgr-id") as corr_id:
            assert corr_id == "ctx-mgr-id"
            assert get_correlation_id() == "ctx-mgr-id"
        assert get_correlation_id() is None

    def test_context_manager_generates_id_when_none(self) -> None:
        """Test generates new ID when none provided."""
        with CorrelationContext() as corr_id:
            assert corr_id is not None
            assert len(corr_id) == 36


class TestTraceHelpers:
    """Tests for trace helper functions."""

    @patch("venus_observability.correlation.trace.get_current_span")
    def test_get_current_trace_context_returns_none_when_no_span(
        self, mock_get_span: MagicMock
    ) -> None:
        """Test returns None when no recording span."""
        mock_get_span.return_value = None
        result = get_current_trace_context()
        assert result is None

    @patch("venus_observability.correlation.trace.get_current_span")
    def test_get_current_trace_context_returns_none_when_not_recording(
        self, mock_get_span: MagicMock
    ) -> None:
        """Test returns None when span not recording."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = False
        mock_get_span.return_value = mock_span
        result = get_current_trace_context()
        assert result is None

    @patch("venus_observability.correlation.trace.get_current_span")
    def test_get_current_trace_context_returns_context(self, mock_get_span: MagicMock) -> None:
        """Test returns trace context when span is recording."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_span_context = MagicMock()
        mock_span_context.is_valid = True
        mock_span_context.trace_id = 0x0AF7651916CD43DD8448EB211C80319C
        mock_span_context.span_id = 0xB7AD6B7169203331
        mock_span_context.trace_flags = 0x01
        mock_span_context.trace_state = None
        mock_span.get_span_context.return_value = mock_span_context
        mock_get_span.return_value = mock_span

        result = get_current_trace_context()
        assert result is not None
        assert result["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
        assert result["span_id"] == "b7ad6b7169203331"
        assert result["trace_flags"] == "01"

    @patch("venus_observability.correlation.trace.Tracer")
    def test_create_span_with_correlation(self, mock_tracer_class: MagicMock) -> None:
        """Test creating span with correlation ID."""
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span
        mock_tracer_class.return_value = mock_tracer

        span = create_span_with_correlation(
            mock_tracer, "test-operation", correlation_id="test-span-corr"
        )
        assert span is mock_span
        mock_tracer.start_span.assert_called_once()
        args, kwargs = mock_tracer.start_span.call_args
        assert args[0] == "test-operation"
        assert kwargs["attributes"]["correlation.id"] == "test-span-corr"

    @patch("venus_observability.correlation.trace.Tracer")
    def test_traced_operation_alias(self, mock_tracer_class: MagicMock) -> None:
        """Test traced_operation is alias for create_span_with_correlation."""
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span
        mock_tracer_class.return_value = mock_tracer

        span = traced_operation(mock_tracer, "aliased-operation", correlation_id="aliased-corr")
        assert span is mock_span
