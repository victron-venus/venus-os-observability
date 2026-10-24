"""Metric mappings and unavailable-value semantics with the real OTel SDK."""

# pylint: disable=protected-access,redefined-outer-name

import math
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry.metrics import CallbackOptions
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint

from venus_observability.metrics import VictronMetrics


@pytest.fixture
def reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def victron_metrics_obj(reader: InMemoryMetricReader) -> Iterator[VictronMetrics]:
    provider = MeterProvider(metric_readers=[reader])
    yield VictronMetrics(provider.get_meter("metric-mappings"))
    provider.shutdown()


def points(reader: InMemoryMetricReader, name: str) -> list[NumberDataPoint]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    values = [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]
    assert all(isinstance(point, NumberDataPoint) for point in values)
    return [point for point in values if isinstance(point, NumberDataPoint)]


class TestVictronMetrics:
    """Tests for VictronMetrics class."""

    def test_init_creates_all_gauges(self) -> None:
        meter = MagicMock()
        VictronMetrics(meter)
        assert meter.create_observable_gauge.call_count == 8
        meter.create_gauge.assert_not_called()

    @pytest.mark.parametrize(
        ("service_kind", "path", "name", "value", "labels"),
        [
            ("battery", "/Soc", "victron.battery.soc", 85.5, {}),
            ("battery", "/Dc/0/Power", "victron.battery.power", -1200.0, {}),
            ("solarcharger", "/Dc/Pv/Power", "victron.pv.power", 3500.0, {}),
            ("grid", "/Ac/Grid/Power", "victron.grid.power", -500.0, {"phase": ""}),
            ("vebus", "/Ac/Loads/Power", "victron.ac.loads", 2000.0, {"phase": ""}),
            ("vebus", "/State", "victron.inverter.state", 3, {}),
            (
                "battery",
                "/Dc/0/Voltages/Cell1",
                "victron.battery.cell.voltage",
                3.45,
                {"cell": "1"},
            ),
            (
                "battery",
                "/Temperatures/Cell1",
                "victron.battery.cell.temperature",
                25.0,
                {"cell": "1"},
            ),
        ],
    )
    def test_update_from_dbus(
        self,
        victron_metrics_obj: VictronMetrics,
        reader: InMemoryMetricReader,
        service_kind: str,
        path: str,
        name: str,
        value: float,
        labels: dict[str, str],
    ) -> None:
        victron_metrics_obj.update_from_dbus(f"com.victronenergy.{service_kind}.ttyO1", path, value)
        (point,) = points(reader, name)
        assert point.value == value
        assert point.attributes == dict(labels, serial="ttyO1")

    def test_extract_serial(self, victron_metrics_obj: VictronMetrics) -> None:
        assert victron_metrics_obj._extract_serial("com.victronenergy.battery.ttyO1") == "ttyO1"
        assert victron_metrics_obj._extract_serial("com.victronenergy.vebus.ttyO2") == "ttyO2"
        assert (
            victron_metrics_obj._extract_serial("com.victronenergy.solarcharger.ttyO0") == "ttyO0"
        )

    @pytest.mark.parametrize(
        "invalid", [None, [], [1], {}, "invalid", math.inf, -math.inf, math.nan]
    )
    def test_update_invalidates_unavailable_values(
        self, victron_metrics_obj: VictronMetrics, reader: InMemoryMetricReader, invalid: Any
    ) -> None:
        """Unavailable input removes OTel's prior sample and retains direct Prometheus NaN."""
        from venus_observability.metrics import battery_soc, update_prometheus_from_dbus

        service = "com.victronenergy.battery.invalid_test"
        for value in (85, invalid, 0):
            victron_metrics_obj.update_from_dbus(service, "/Soc", value)
            update_prometheus_from_dbus(service, "/Soc", value)
            observed = points(reader, "victron.battery.soc")
            prometheus = battery_soc.labels(serial="invalid_test")._value.get()
            if value is invalid:
                assert observed == []
                assert math.isnan(prometheus)
                assert not victron_metrics_obj._active_gauges
            else:
                assert len(observed) == 1
                assert observed[0].value == prometheus == value

    def test_update_with_attributes(
        self, victron_metrics_obj: VictronMetrics, reader: InMemoryMetricReader
    ) -> None:
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.battery.ttyO1", "/Soc", 50.0, {"location": "home"}
        )
        (point,) = points(reader, "victron.battery.soc")
        assert point.attributes == {"location": "home", "serial": "ttyO1"}

    def test_callback_snapshot_cannot_change_retained_attributes(
        self, victron_metrics_obj: VictronMetrics, reader: InMemoryMetricReader
    ) -> None:
        """A consumer of one collection cannot redirect a later series/removal."""
        service = "com.victronenergy.battery.snapshot_test"
        victron_metrics_obj.update_from_dbus(service, "/Soc", 5, {"zones": ["garage"]})
        snapshot = victron_metrics_obj.battery_soc.observe(CallbackOptions())
        assert isinstance(snapshot[0].attributes, dict)
        snapshot[0].attributes["serial"] = "different"
        (point,) = points(reader, "victron.battery.soc")
        assert point.attributes == {"serial": "snapshot_test", "zones": ("garage",)}
        victron_metrics_obj.invalidate_service(service)
        assert points(reader, "victron.battery.soc") == []


class TestRealVenusPaths:
    """Tests for mappings of paths actually emitted by Venus OS."""

    def test_otelside_yield_power_maps_to_pv(
        self, victron_metrics_obj: VictronMetrics, reader: InMemoryMetricReader
    ) -> None:
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.solarcharger.ttyUSB3", "/Yield/Power", 1048.0
        )
        (point,) = points(reader, "victron.pv.power")
        assert point.value == 1048 and point.attributes == {"serial": "ttyUSB3"}

    def test_prometheus_yield_power_maps_to_pv(self) -> None:
        from venus_observability.metrics import pv_power, update_prometheus_from_dbus

        update_prometheus_from_dbus(
            "com.victronenergy.solarcharger.ttyUSB3", "/Yield/Power", 1200.0
        )
        assert pv_power.labels(serial="ttyUSB3")._value.get() == 1200.0

    def test_prometheus_per_phase_grid_and_consumption(self) -> None:
        from venus_observability.metrics import ac_loads, grid_power, update_prometheus_from_dbus

        update_prometheus_from_dbus("com.victronenergy.system", "/Ac/Grid/L1/Power", 128.0)
        update_prometheus_from_dbus("com.victronenergy.system", "/Ac/Grid/L2/Power", 7.0)
        update_prometheus_from_dbus("com.victronenergy.system", "/Ac/Consumption/L1/Power", 577.0)
        assert grid_power.labels(serial="system", phase="l1")._value.get() == 128.0
        assert grid_power.labels(serial="system", phase="l2")._value.get() == 7.0
        assert ac_loads.labels(serial="system", phase="l1")._value.get() == 577.0


class TestPvinverterAcPower:
    """dbus-tasmota-pv pvinverter services report PV production as /Ac/Power."""

    def test_prometheus_pvinverter_ac_power_maps_to_pv(self) -> None:
        from venus_observability.metrics import pv_power, update_prometheus_from_dbus

        update_prometheus_from_dbus("com.victronenergy.pvinverter.tasmota_9895", "/Ac/Power", 850.0)
        assert pv_power.labels(serial="tasmota_9895")._value.get() == 850.0

    def test_grid_service_ac_power_does_not_map_to_pv(self) -> None:
        from venus_observability.metrics import pv_power, update_prometheus_from_dbus

        update_prometheus_from_dbus("com.victronenergy.grid.ve_X", "/Ac/Power", 500.0)
        assert pv_power.labels(serial="ve_X")._value.get() == 0.0
