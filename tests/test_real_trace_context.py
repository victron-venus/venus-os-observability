"""Use real OpenTelemetry carriers across the registered propagation boundary."""

from opentelemetry import baggage, trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState

from venus_observability.correlation import (
    CorrelationIDPropagator,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_real_default_getter_extracts_w3c_context() -> None:
    context = CorrelationIDPropagator().extract(
        {"traceparent": TRACEPARENT, "tracestate": "vendor=value"}
    )
    parent = trace.get_current_span(context).get_span_context()
    assert parent.is_remote
    assert parent.trace_id == int("0af7651916cd43dd8448eb211c80319c", 16)
    assert dict(parent.trace_state) == {"vendor": "value"}


def test_oversized_tracestate_keeps_parent_but_discards_members() -> None:
    header = ",".join(f"k{i}={'v' * 256}" for i in range(32))
    assert len(header) > 8192
    context = CorrelationIDPropagator().extract({"traceparent": TRACEPARENT, "tracestate": header})
    parent = trace.get_current_span(context).get_span_context()
    assert parent.is_remote
    assert parent.span_id == int("b7ad6b7169203331", 16)
    assert not parent.trace_state


def test_real_default_setter_round_trips_explicit_context() -> None:
    span = SpanContext(123, 456, False, TraceFlags(1), TraceState([("vendor", "value")]))
    context = trace.set_span_in_context(NonRecordingSpan(span), Context())
    carrier: dict[str, str] = {}
    propagator = CorrelationIDPropagator()
    propagator.inject(carrier, context=context)
    restored = trace.get_current_span(propagator.extract(carrier)).get_span_context()
    assert (restored.trace_id, restored.span_id) == (span.trace_id, span.span_id)
    assert dict(restored.trace_state) == {"vendor": "value"}


def test_extract_returns_correlation_without_mutating_ambient_state() -> None:
    token = set_correlation_id("ambient")
    try:
        context = CorrelationIDPropagator().extract({"x-correlation-id": "request-A"})
        assert baggage.get_baggage("correlation-id", context=context) == "request-A"
        assert get_correlation_id() == "ambient"
    finally:
        reset_correlation_id(token)


def test_empty_carrier_does_not_inherit_previous_extraction() -> None:
    propagator = CorrelationIDPropagator()
    token = set_correlation_id(None)
    try:
        propagator.extract({"x-correlation-id": "request-A"})
        second = propagator.extract({}, context=Context())
        outbound: dict[str, str] = {}
        propagator.inject(outbound, context=second)
        assert outbound["x-correlation-id"] != "request-A"
        assert "request-A" not in outbound["baggage"]
        assert get_correlation_id() is None
    finally:
        reset_correlation_id(token)


def test_explicit_context_isolates_correlation_and_trace_from_ambient() -> None:
    span_a = NonRecordingSpan(SpanContext(111, 222, False, TraceFlags(1)))
    span_b = NonRecordingSpan(SpanContext(123, 456, False, TraceFlags(1)))
    context = trace.set_span_in_context(span_b, Context())
    context = baggage.set_baggage("correlation-id", "explicit-B", context=context)
    token = set_correlation_id("ambient-A")
    try:
        with trace.use_span(span_a):
            outbound: dict[str, str] = {}
            CorrelationIDPropagator().inject(outbound, context=context)
        assert outbound["traceparent"] == "00-0000000000000000000000000000007b-00000000000001c8-01"
        assert outbound["x-correlation-id"] == "explicit-B"
        assert (
            outbound["baggage"]
            == "correlation-id=explicit-B, trace-id=0000000000000000000000000000007b"
        )
    finally:
        reset_correlation_id(token)


def test_explicit_empty_context_cannot_fall_back_to_ambient_id() -> None:
    token = set_correlation_id("ambient-A")
    try:
        outbound: dict[str, str] = {}
        CorrelationIDPropagator().inject(outbound, context=Context())
        assert outbound["x-correlation-id"] != "ambient-A"
        assert "ambient-A" not in outbound["baggage"]
        assert "traceparent" not in outbound
    finally:
        reset_correlation_id(token)


def test_implicit_context_preserves_legacy_explicitly_scoped_id() -> None:
    token = set_correlation_id("legacy-scoped-id")
    try:
        outbound: dict[str, str] = {}
        CorrelationIDPropagator().inject(outbound)
        assert outbound["x-correlation-id"] == "legacy-scoped-id"
    finally:
        reset_correlation_id(token)
