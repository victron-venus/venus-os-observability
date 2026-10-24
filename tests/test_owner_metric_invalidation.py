"""Owner loss removes only the lost publisher's existing OpenTelemetry series."""

# pylint: disable=protected-access

import math
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint

from venus_observability import metrics


@pytest.fixture
def reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def collector(reader: InMemoryMetricReader) -> Iterator[metrics.VictronMetrics]:
    """Exercise real SDK collection, including its rejection of NaN observations."""
    provider = MeterProvider(metric_readers=[reader])
    yield metrics.VictronMetrics(provider.get_meter("owner-loss-regression"))
    provider.shutdown()


def exported(reader: InMemoryMetricReader, name: str) -> list[NumberDataPoint]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    points = [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]
    assert all(isinstance(point, NumberDataPoint) for point in points)
    return [point for point in points if isinstance(point, NumberDataPoint)]


@pytest.mark.parametrize(
    ("path", "otel_name", "prom_name", "extra_labels"),
    [
        ("/Soc", "victron.battery.soc", "battery_soc", {}),
        ("/Dc/0/Power", "victron.battery.power", "battery_power", {}),
        ("/Yield/Power", "victron.pv.power", "pv_power", {}),
        ("/Ac/Power", "victron.pv.power", "pv_power", {}),
        ("/Ac/Grid/Power", "victron.grid.power", "grid_power", {"phase": ""}),
        ("/Ac/Grid/L2/Power", "victron.grid.power", "grid_power", {"phase": "l2"}),
        ("/Ac/Loads/Power", "victron.ac.loads", "ac_loads", {"phase": ""}),
        ("/Ac/Consumption/L1/Power", "victron.ac.loads", "ac_loads", {"phase": "l1"}),
        ("/State", "victron.inverter.state", "inverter_state", {}),
        ("/Dc/0/Voltages/Cell2", "victron.battery.cell.voltage", "cell_voltages", {"cell": "2"}),
        (
            "/Temperatures/Cell2",
            "victron.battery.cell.temperature",
            "cell_temperature",
            {"cell": "2"},
        ),
    ],
)
def test_owner_loss_invalidates_both_exporters_and_recovers_measured_zero(
    collector: metrics.VictronMetrics,
    reader: InMemoryMetricReader,
    path: str,
    otel_name: str,
    prom_name: str,
    extra_labels: dict[str, str],
) -> None:
    """All mapped gauge types become unavailable, then accept fresh zero normally."""
    service = "com.victronenergy.pvinverter.owner_loss_test"
    labels = dict(extra_labels, serial="owner_loss_test")
    prom = getattr(metrics, prom_name)
    collector.update_from_dbus(service, path, 37)
    metrics.update_prometheus_from_dbus(service, path, 37)
    (point,) = exported(reader, otel_name)
    assert point.value == 37 and point.attributes == labels
    counter = metrics.dbus_signals_received.labels(service=service, path=path, interface="")
    before_counter = counter._value.get()
    before_labels = len(prom._metrics)

    collector.invalidate_service(service)
    metrics.invalidate_prometheus_service(service)

    assert exported(reader, otel_name) == []
    assert math.isnan(prom.labels(**labels)._value.get())
    assert len(prom._metrics) == before_labels
    assert counter._value.get() == before_counter
    assert not collector._active_gauges
    assert service not in metrics._prometheus_publishers.values()

    collector.invalidate_service(service)
    metrics.invalidate_prometheus_service(service)
    assert exported(reader, otel_name) == []

    collector.update_from_dbus(service, path, 0)
    metrics.update_prometheus_from_dbus(service, path, 0)
    (point,) = exported(reader, otel_name)
    assert point.value == 0 and point.attributes == labels
    assert prom.labels(**labels)._value.get() == 0


def test_suffix_collision_keeps_the_latest_live_publisher(
    collector: metrics.VictronMetrics, reader: InMemoryMetricReader
) -> None:
    """Losing a battery must not clear a newer VE.Bus reading sharing its suffix."""
    older = "com.victronenergy.battery.shared_owner_test"
    newer = "com.victronenergy.vebus.shared_owner_test"
    for service, value in ((older, 1), (newer, 3)):
        collector.update_from_dbus(service, "/State", value)
        metrics.update_prometheus_from_dbus(service, "/State", value)
    collector.invalidate_service(older)
    metrics.invalidate_prometheus_service(older)
    (point,) = exported(reader, "victron.inverter.state")
    assert point.value == 3 and point.attributes == {"serial": "shared_owner_test"}
    assert metrics.inverter_state.labels(serial="shared_owner_test")._value.get() == 3
    collector.invalidate_service(newer)
    metrics.invalidate_prometheus_service(newer)
    assert exported(reader, "victron.inverter.state") == []
    assert math.isnan(metrics.inverter_state.labels(serial="shared_owner_test")._value.get())


def test_distinct_alias_and_other_phase_remain_valid(
    collector: metrics.VictronMetrics, reader: InMemoryMetricReader
) -> None:
    """Invalidate exact series, including a same-suffix publisher on another phase."""
    lost = "com.victronenergy.grid.independent_owner_test"
    same_suffix = "com.victronenergy.system.independent_owner_test"
    alias = "com.victronenergy.grid.live_alias_test"
    for service, path, value in (
        (lost, "/Ac/Grid/L1/Power", 10),
        (same_suffix, "/Ac/Grid/L2/Power", 20),
        (alias, "/Ac/Grid/L1/Power", 30),
    ):
        collector.update_from_dbus(service, path, value)
        metrics.update_prometheus_from_dbus(service, path, value)
    assert len(exported(reader, "victron.grid.power")) == 3
    collector.invalidate_service(lost)
    metrics.invalidate_prometheus_service(lost)
    remaining = exported(reader, "victron.grid.power")
    observed = set()
    for point in remaining:
        assert point.attributes is not None
        observed.add((point.attributes["serial"], point.attributes["phase"], point.value))
    assert observed == {("independent_owner_test", "l2", 20), ("live_alias_test", "l1", 30)}
    assert math.isnan(
        metrics.grid_power.labels(serial="independent_owner_test", phase="l1")._value.get()
    )
    assert metrics.grid_power.labels(serial="independent_owner_test", phase="l2")._value.get() == 20
    assert metrics.grid_power.labels(serial="live_alias_test", phase="l1")._value.get() == 30


def test_unknown_loss_creates_no_gauges_or_labels(
    collector: metrics.VictronMetrics, reader: InMemoryMetricReader
) -> None:
    """Discovery alone must never create placeholder metric series."""
    before = tuple(len(gauge._metrics) for gauge in (metrics.battery_soc, metrics.grid_power))
    collector.invalidate_service("com.victronenergy.battery.never_published_test")
    metrics.invalidate_prometheus_service("com.victronenergy.battery.never_published_test")
    assert (
        tuple(len(gauge._metrics) for gauge in (metrics.battery_soc, metrics.grid_power)) == before
    )
    assert exported(reader, "victron.battery.soc") == []
    assert exported(reader, "victron.grid.power") == []


def test_invalidation_retains_original_otel_attributes(
    collector: metrics.VictronMetrics, reader: InMemoryMetricReader
) -> None:
    """A later caller mutation cannot redirect invalidation to a different series."""
    attrs = {"zones": ["garage"]}
    service = "com.victronenergy.battery.attribute_owner_test"
    collector.update_from_dbus(service, "/Soc", 42, attrs)
    attrs["zones"].append("house")
    assert "serial" not in attrs
    (point,) = exported(reader, "victron.battery.soc")
    assert point.attributes == {"serial": "attribute_owner_test", "zones": ("garage",)}
    collector.invalidate_service(service)
    assert exported(reader, "victron.battery.soc") == []


def test_repeated_owner_churn_does_not_retain_invalidation_bookkeeping(
    collector: metrics.VictronMetrics, reader: InMemoryMetricReader
) -> None:
    """Repeated restart/loss cycles leave no per-owner tracking or stale exports."""
    for value in range(100):
        service = f"com.victronenergy.battery.owner_churn_{value}"
        collector.update_from_dbus(service, "/Soc", value)
        metrics.update_prometheus_from_dbus(service, "/Soc", value)
        (point,) = exported(reader, "victron.battery.soc")
        assert point.value == value
        collector.invalidate_service(service)
        metrics.invalidate_prometheus_service(service)
        assert not collector._active_gauges
        assert service not in metrics._prometheus_publishers.values()
        assert exported(reader, "victron.battery.soc") == []
        assert not collector.battery_soc._samples


def test_real_otel_export_marks_loss_unavailable_and_accepts_fresh_zero(
    collector: metrics.VictronMetrics, reader: InMemoryMetricReader
) -> None:
    """No sample means unavailable; neither stale42 nor an invented0 is exported."""
    service = "com.victronenergy.battery.sdk_owner_test"
    collector.update_from_dbus(service, "/Soc", 42)
    (point,) = exported(reader, "victron.battery.soc")
    assert point.value == 42
    collector.invalidate_service(service)
    assert exported(reader, "victron.battery.soc") == []
    assert exported(reader, "victron.battery.soc") == []
    collector.update_from_dbus(service, "/Soc", 0)
    (point,) = exported(reader, "victron.battery.soc")
    assert point.value == 0 and point.attributes == {"serial": "sdk_owner_test"}


def test_real_prometheus_exporter_and_otlp_reader_share_unavailable_semantics() -> None:
    """Independent readers both omit lost samples and both retain a fresh zero."""
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from prometheus_client import CollectorRegistry

    registry = CollectorRegistry()
    prometheus_reader = PrometheusMetricReader(
        registry=registry, disable_target_info=True, scope_info_enabled=False
    )
    memory_reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[prometheus_reader, memory_reader])
    collector = metrics.VictronMetrics(provider.get_meter("two-reader-loss"))
    service = "com.victronenergy.battery.two_reader_test"

    def prometheus_values() -> list[float]:
        return [
            sample.value
            for family in registry.collect()
            for sample in family.samples
            if sample.name.startswith("victron_battery_soc")
            and sample.labels.get("serial") == "two_reader_test"
        ]

    try:
        collector.update_from_dbus(service, "/Soc", 42)
        assert prometheus_values() == [42]
        assert [point.value for point in exported(memory_reader, "victron.battery.soc")] == [42]
        assert prometheus_values() == [42]
        collector.invalidate_service(service)
        assert exported(memory_reader, "victron.battery.soc") == []
        assert prometheus_values() == []
        assert prometheus_values() == []
        collector.update_from_dbus(service, "/Soc", 0)
        assert prometheus_values() == [0]
        assert [point.value for point in exported(memory_reader, "victron.battery.soc")] == [0]
    finally:
        provider.shutdown()
