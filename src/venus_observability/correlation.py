"""
Correlation ID utilities for distributed tracing across MQTT → D-Bus → inverter pipeline.
"""

import contextvars
import logging
import uuid
from typing import Any

from opentelemetry import trace
from opentelemetry.baggage import set_baggage
from opentelemetry.context import Context
from opentelemetry.propagators import textmap

logger = logging.getLogger(__name__)

# Context variable for correlation ID propagation within async context
_correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

# W3C Trace Context header names
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"
BAGGAGE_HEADER = "baggage"

# Custom header for correlation ID (simpler for MQTT)
CORRELATION_ID_HEADER = "x-correlation-id"


def generate_correlation_id() -> str:
    """Generate a new correlation ID (UUID v4)."""
    return str(uuid.uuid4())


def get_correlation_id() -> str | None:
    """Get current correlation ID from context."""
    return _correlation_id_var.get()


def set_correlation_id(correlation_id: str | None) -> contextvars.Token[str | None]:
    """Set correlation ID in context. Returns token for reset."""
    return _correlation_id_var.set(correlation_id)


def reset_correlation_id(token: contextvars.Token[str | None]) -> None:
    """Reset correlation ID from token."""
    _correlation_id_var.reset(token)


def _get_header_case_insensitive(headers: dict[str, str], name: str) -> str | None:
    """Get header value case-insensitively."""
    return headers.get(name) or headers.get(name.lower())


def _extract_from_traceparent(traceparent: str) -> str | None:
    """Extract trace ID from W3C traceparent header."""
    parts = traceparent.split("-")
    if len(parts) >= 2:
        return parts[1]
    return None


def _extract_from_baggage(baggage: str) -> str | None:
    """Extract correlation ID from baggage header."""
    for item in baggage.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            if key.strip().lower() == "correlation-id":
                return value.strip()
    return None


def extract_correlation_id_from_headers(headers: dict[str, str]) -> str | None:
    """
    Extract correlation ID from message headers.
    Checks in order: W3C traceparent, custom x-correlation-id, baggage.
    """
    # Check W3C traceparent header
    traceparent = _get_header_case_insensitive(headers, TRACEPARENT_HEADER)
    if traceparent:
        result = _extract_from_traceparent(traceparent)
        if result:
            return result

    # Check custom correlation ID header
    corr_id = _get_header_case_insensitive(headers, CORRELATION_ID_HEADER)
    if corr_id:
        return corr_id

    # Check baggage header
    baggage = _get_header_case_insensitive(headers, BAGGAGE_HEADER)
    if baggage:
        result = _extract_from_baggage(baggage)
        if result:
            return result

    return None


def inject_correlation_id_into_headers(
    headers: dict[str, str],
    correlation_id: str | None = None,
    trace_context: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Inject correlation ID and trace context into message headers.
    Returns updated headers dict.
    """
    corr_id = correlation_id or get_correlation_id() or generate_correlation_id()

    # Set custom correlation ID header
    headers[CORRELATION_ID_HEADER] = corr_id

    # Set W3C traceparent if we have trace context
    if trace_context:
        trace_id = trace_context.get("trace_id")
        span_id = trace_context.get("span_id")
        trace_flags = trace_context.get("trace_flags", "01")  # sampled
        if trace_id and span_id:
            headers[TRACEPARENT_HEADER] = f"00-{trace_id}-{span_id}-{trace_flags}"

        trace_state = trace_context.get("trace_state")
        if trace_state:
            headers[TRACESTATE_HEADER] = trace_state

    # Set baggage
    baggage_parts = [f"correlation-id={corr_id}"]
    if trace_context:
        trace_id = trace_context.get("trace_id")
        if trace_id:
            baggage_parts.append(f"trace-id={trace_id}")
    headers[BAGGAGE_HEADER] = ", ".join(baggage_parts)

    return headers


def get_current_trace_context() -> dict[str, str] | None:
    """Get current OpenTelemetry trace context for propagation."""
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return None

    span_context = span.get_span_context()
    if not span_context or not span_context.is_valid:
        return None

    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
        "trace_flags": format(span_context.trace_flags, "02x"),
        "trace_state": str(span_context.trace_state) if span_context.trace_state else "",
    }


class CorrelationIDPropagator(textmap.TextMapPropagator):
    """
    W3C Trace Context propagator with correlation ID support.
    Can be registered with OpenTelemetry for automatic propagation.
    """

    # type: ignore[override] - signature mismatch with parent class
    def extract(
        self,
        carrier: textmap.CarrierT,
        getter: textmap.Getter[textmap.CarrierT] = textmap.default_getter,
        context: Context | None = None,
    ) -> Context:
        """Extract trace context and correlation ID from carrier."""
        headers_dict: dict[str, str] = dict(getter(carrier))
        correlation_id = extract_correlation_id_from_headers(headers_dict)

        if correlation_id:
            # Set in context variable
            _ = set_correlation_id(correlation_id)
            # Note: token should be stored and reset after processing

        # Delegate to standard W3C propagator for trace context
        from opentelemetry.propagators.tracecontext import TraceContextTextMapPropagator

        w3c = TraceContextTextMapPropagator()
        return w3c.extract(carrier, getter, context)  # type: ignore[no-any-return,operator]

    # type: ignore[override] - signature mismatch with parent class
    def inject(
        self,
        carrier: textmap.CarrierT,
        setter: textmap.Setter[textmap.CarrierT] = textmap.default_setter,
        context: Context | None = None,
    ) -> None:
        """Inject trace context and correlation ID into carrier."""
        # Get correlation ID from context or current span
        correlation_id = get_correlation_id()
        trace_context = get_current_trace_context()

        headers: dict[str, str] = {}
        inject_correlation_id_into_headers(headers, correlation_id, trace_context)

        for key, value in headers.items():
            setter(carrier, key, value)  # type: ignore[operator]

        # Also inject standard W3C trace context
        from opentelemetry.propagators.tracecontext import TraceContextTextMapPropagator

        w3c = TraceContextTextMapPropagator()
        w3c.inject(carrier, setter, context)  # type: ignore[operator]

    @property
    def fields(self) -> set[str]:
        """Return header fields used by this propagator."""
        return {
            TRACEPARENT_HEADER,
            TRACESTATE_HEADER,
            BAGGAGE_HEADER,
            CORRELATION_ID_HEADER,
        }


def create_span_with_correlation(
    tracer: trace.Tracer,
    name: str,
    correlation_id: str | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> trace.Span:
    """
    Create a span with correlation ID injected into attributes and baggage.
    """
    corr_id = correlation_id or get_correlation_id() or generate_correlation_id()

    # Set correlation ID in context for downstream operations
    _ = set_correlation_id(corr_id)

    attrs = attributes or {}
    attrs["correlation.id"] = corr_id

    span = tracer.start_span(name, kind=kind, attributes=attrs)

    # Add correlation ID to baggage for propagation
    ctx = trace.set_span_in_context(span)
    ctx = set_baggage("correlation-id", corr_id, context=ctx)

    return span


# MQTT-specific helpers
MQTT_CORRELATION_PROPERTIES = [
    "correlation_id",
    "correlationId",
    "x-correlation-id",
    "trace_id",
    "traceId",
    "traceparent",
]


def extract_correlation_id_from_mqtt_properties(
    properties: Any,
) -> str | None:
    """Extract correlation ID from MQTT v5 properties object."""
    if not properties:
        return None

    # Try UserProperty list
    if hasattr(properties, "UserProperty"):
        for key, value in properties.UserProperty or []:
            if key.lower() in [k.lower() for k in MQTT_CORRELATION_PROPERTIES]:
                return str(value)

    # Try direct attributes
    for prop in MQTT_CORRELATION_PROPERTIES:
        if hasattr(properties, prop):
            value = getattr(properties, prop)
            if value:
                return str(value)

    return None


def inject_correlation_id_into_mqtt_properties(
    properties: Any,
    correlation_id: str | None = None,
) -> Any:
    """Inject correlation ID into MQTT v5 properties object."""
    if not properties or not hasattr(properties, "UserProperty"):
        return properties

    corr_id = correlation_id or get_correlation_id() or generate_correlation_id()

    # Add to UserProperties
    user_props = list(properties.UserProperty or [])
    user_props.append(("correlation_id", corr_id))
    user_props.append(("trace_id", corr_id))  # Use correlation ID as trace ID for simplicity

    properties.UserProperty = user_props
    return properties


# Context manager for correlation ID scoping
class CorrelationContext:
    """Context manager for correlation ID propagation."""

    def __init__(self, correlation_id: str | None = None):
        self.correlation_id = correlation_id or get_correlation_id() or generate_correlation_id()
        self.token: contextvars.Token[str | None] | None = None

    def __enter__(self) -> str:
        self.token = set_correlation_id(self.correlation_id)
        return self.correlation_id

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.token:
            reset_correlation_id(self.token)


# Convenience function for creating correlated spans
def traced_operation(
    tracer: trace.Tracer,
    operation_name: str,
    correlation_id: str | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> trace.Span:
    """
    Context manager for a traced operation with correlation ID.
    Usage:
        with traced_operation(tracer, "mqtt.publish", correlation_id) as span:
            # do work
    """
    return create_span_with_correlation(
        tracer=tracer,
        name=operation_name,
        correlation_id=correlation_id,
        kind=kind,
        attributes=attributes,
    )
