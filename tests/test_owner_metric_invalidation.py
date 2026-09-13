"""Owner loss invalidates only the lost publisher's existing metric series."""

# pylint: disable=protected-access

import math
from typing import cast
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from venus_observability import metrics


@pytest.fixture
def collector() -> metrics.VictronMetrics:
    """Use distinct instruments so series ownership follows real meter behavior."""
    meter = MagicMock()
    meter.create_gauge.side_effect = lambda **_kwargs: MagicMock()
    return metrics.VictronMetrics(meter)


@pytest.mark.parametrize(
    ("path", "otel_name", "prom_name", "extra_labels"),
    [
        ("/Soc", "battery_soc", "battery_soc", {}),
        ("/Dc/0/Power", "battery_power", "battery_power", {}),
        ("/Yield/Power", "pv_power", "pv_power", {}),
        ("/Ac/Power", "pv_power", "pv_power", {}),
        ("/Ac/Grid/Power", "grid_power", "grid_power", {"phase": ""}),
        ("/Ac/Grid/L2/Power", "grid_power", "grid_power", {"phase": "l2"}),
        ("/Ac/Loads/Power", "ac_loads", "ac_loads", {"phase": ""}),
        ("/Ac/Consumption/L1/Power", "ac_loads", "ac_loads", {"phase": "l1"}),
        ("/State", "inverter_state", "inverter_state", {}),
        ("/Dc/0/Voltages/Cell2", "cell_voltage", "cell_voltages", {"cell": "2"}),
        ("/Temperatures/Cell2", "cell_temperature", "cell_temperature", {"cell": "2"}),
    ],
)
def test_owner_loss_invalidates_both_exporters_and_recovers_measured_zero(
    collector: metrics.VictronMetrics,
    path: str,
    otel_name: str,
    prom_name: str,
    extra_labels: dict[str, str],
) -> None:
    """All mapped gauge types become unavailable, then accept fresh zero normally."""
    service = "com.victronenergy.pvinverter.owner_loss_test"
    labels = dict(extra_labels, serial="owner_loss_test")
    otel = getattr(collector, otel_name)
    prom = getattr(metrics, prom_name)
    collector.update_from_dbus(service, path, 37)
    metrics.update_prometheus_from_dbus(service, path, 37)
    counter = metrics.dbus_signals_received.labels(service=service, path=path, interface="")
    before_counter = counter._value.get()
    before_labels = len(prom._metrics)

    collector.invalidate_service(service)
    metrics.invalidate_prometheus_service(service)

    assert math.isnan(otel.set.call_args.args[0])
    assert otel.set.call_args.args[1] == labels
    assert math.isnan(prom.labels(**labels)._value.get())
    assert len(prom._metrics) == before_labels
    assert counter._value.get() == before_counter
    assert not collector._active_gauges
    assert service not in metrics._prometheus_publishers.values()

    before_calls = otel.set.call_count
    collector.invalidate_service(service)
    metrics.invalidate_prometheus_service(service)
    assert otel.set.call_count == before_calls

    collector.update_from_dbus(service, path, 0)
    metrics.update_prometheus_from_dbus(service, path, 0)
    assert otel.set.call_args.args[0] == 0
    assert prom.labels(**labels)._value.get() == 0


def test_suffix_collision_keeps_the_latest_live_publisher(
    collector: metrics.VictronMetrics,
) -> None:
    """Losing a battery must not clear a newer VE.Bus reading sharing its suffix."""
    older = "com.victronenergy.battery.shared_owner_test"
    newer = "com.victronenergy.vebus.shared_owner_test"
    state = cast(MagicMock, collector.inverter_state)
    for service, value in ((older, 1), (newer, 3)):
        collector.update_from_dbus(service, "/State", value)
        metrics.update_prometheus_from_dbus(service, "/State", value)
    before_calls = state.set.call_count

    collector.invalidate_service(older)
    metrics.invalidate_prometheus_service(older)

    assert state.set.call_count == before_calls
    assert metrics.inverter_state.labels(serial="shared_owner_test")._value.get() == 3
    collector.invalidate_service(newer)
    metrics.invalidate_prometheus_service(newer)
    assert math.isnan(state.set.call_args.args[0])
    assert math.isnan(metrics.inverter_state.labels(serial="shared_owner_test")._value.get())


def test_distinct_alias_and_other_phase_remain_valid(
    collector: metrics.VictronMetrics,
) -> None:
    """Invalidate exact series, including a same-suffix publisher on another phase."""
    lost = "com.victronenergy.grid.independent_owner_test"
    same_suffix = "com.victronenergy.system.independent_owner_test"
    alias = "com.victronenergy.grid.live_alias_test"
    grid = cast(MagicMock, collector.grid_power)
    for service, path, value in (
        (lost, "/Ac/Grid/L1/Power", 10),
        (same_suffix, "/Ac/Grid/L2/Power", 20),
        (alias, "/Ac/Grid/L1/Power", 30),
    ):
        collector.update_from_dbus(service, path, value)
        metrics.update_prometheus_from_dbus(service, path, value)
    before_calls = grid.set.call_count
    collector.invalidate_service(lost)
    metrics.invalidate_prometheus_service(lost)
    assert grid.set.call_count == before_calls + 1
    assert grid.set.call_args.args[1] == {
        "serial": "independent_owner_test",
        "phase": "l1",
    }
    assert math.isnan(
        metrics.grid_power.labels(serial="independent_owner_test", phase="l1")._value.get()
    )
    assert metrics.grid_power.labels(serial="independent_owner_test", phase="l2")._value.get() == 20
    assert metrics.grid_power.labels(serial="live_alias_test", phase="l1")._value.get() == 30


def test_unknown_loss_creates_no_gauges_or_labels(collector: metrics.VictronMetrics) -> None:
    """Discovery alone must never create placeholder metric series."""
    before = tuple(len(gauge._metrics) for gauge in (metrics.battery_soc, metrics.grid_power))
    collector.invalidate_service("com.victronenergy.battery.never_published_test")
    metrics.invalidate_prometheus_service("com.victronenergy.battery.never_published_test")
    assert (
        tuple(len(gauge._metrics) for gauge in (metrics.battery_soc, metrics.grid_power)) == before
    )
    cast(MagicMock, collector.battery_soc).set.assert_not_called()
    cast(MagicMock, collector.grid_power).set.assert_not_called()


def test_invalidation_retains_original_otel_attributes(collector: metrics.VictronMetrics) -> None:
    """A later caller mutation cannot redirect invalidation to a different series."""
    attrs = {"zones": ["garage"]}
    service = "com.victronenergy.battery.attribute_owner_test"
    collector.update_from_dbus(service, "/Soc", 42, attrs)
    attrs["zones"].append("house")
    assert "serial" not in attrs
    collector.invalidate_service(service)
    assert cast(MagicMock, collector.battery_soc).set.call_args.args[1] == {
        "serial": "attribute_owner_test",
        "zones": ("garage",),
    }


def test_repeated_owner_churn_does_not_retain_invalidation_bookkeeping(
    collector: metrics.VictronMetrics,
) -> None:
    """Repeated restart/loss cycles leave no per-owner tracking entries behind."""
    service = "com.victronenergy.battery.owner_churn_test"
    for value in range(100):
        collector.update_from_dbus(service, "/Soc", value)
        metrics.update_prometheus_from_dbus(service, "/Soc", value)
        collector.invalidate_service(service)
        metrics.invalidate_prometheus_service(service)
        assert not collector._active_gauges
        assert service not in metrics._prometheus_publishers.values()


def test_real_otel_export_marks_loss_unavailable_and_accepts_fresh_zero() -> None:
    """Verify the real SDK exports NaN rather than retaining its prior finite sample."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    collector = metrics.VictronMetrics(provider.get_meter("owner-loss-regression"))
    service = "com.victronenergy.battery.sdk_owner_test"

    def exported_soc() -> float:
        data = reader.get_metrics_data()
        assert data is not None
        points = [
            point
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == "victron.battery.soc"
            for point in metric.data.data_points
        ]
        assert len(points) == 1
        assert points[0].attributes == {"serial": "sdk_owner_test"}
        return float(points[0].value)

    try:
        collector.update_from_dbus(service, "/Soc", 42)
        assert exported_soc() == 42
        collector.invalidate_service(service)
        assert math.isnan(exported_soc())
        collector.update_from_dbus(service, "/Soc", 0)
        assert exported_soc() == 0
    finally:
        provider.shutdown()
