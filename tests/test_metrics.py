"""
Test configuration and fixtures for venus-os-observability.
"""

# pylint: disable=import-error,wrong-import-position,protected-access
# pylint: disable=redefined-outer-name,unused-variable,unused-argument

import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "src")
from venus_observability.metrics import VictronMetrics


@pytest.fixture
def mock_otel_meter() -> MagicMock:
    """Create a mock OpenTelemetry meter."""
    meter: MagicMock = MagicMock()
    # Create separate mocks for each gauge to track calls individually
    meter.create_gauge.side_effect = lambda *args, **kwargs: MagicMock()
    return meter


@pytest.fixture
def victron_metrics_obj(mock_otel_meter: MagicMock) -> VictronMetrics:
    """Create VictronMetrics with mock meter."""
    return VictronMetrics(mock_otel_meter)


class TestVictronMetrics:
    """Tests for VictronMetrics class."""

    def test_init_creates_all_gauges(
        self, mock_otel_meter: MagicMock, victron_metrics_obj: VictronMetrics
    ) -> None:
        """Verify all expected gauges are created."""
        assert mock_otel_meter.create_gauge.call_count == 8

    def test_update_from_dbus_soc(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test SOC metric update."""
        victron_metrics_obj.update_from_dbus("com.victronenergy.battery.ttyO1", "/Soc", 85.5)
        victron_metrics_obj.battery_soc.set.assert_called()  # type: ignore[attr-defined]

    def test_update_from_dbus_battery_power(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test battery power metric update."""
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.battery.ttyO1", "/Dc/0/Power", -1200.0
        )
        victron_metrics_obj.battery_power.set.assert_called()  # type: ignore[attr-defined]

    def test_update_from_dbus_pv_power(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test PV power metric update."""
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.solarcharger.ttyO0", "/Dc/Pv/Power", 3500.0
        )
        victron_metrics_obj.pv_power.set.assert_called()  # type: ignore[attr-defined]

    def test_update_from_dbus_grid_power(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test grid power metric update."""
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.grid.ttyO3", "/Ac/Grid/Power", -500.0
        )
        victron_metrics_obj.grid_power.set.assert_called()  # type: ignore[attr-defined]

    def test_update_from_dbus_ac_loads(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test AC loads metric update."""
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.vebus.ttyO2", "/Ac/Loads/Power", 2000.0
        )
        victron_metrics_obj.ac_loads.set.assert_called()  # type: ignore[attr-defined]

    def test_update_from_dbus_inverter_state(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test inverter state metric update."""
        victron_metrics_obj.update_from_dbus("com.victronenergy.vebus.ttyO2", "/State", 3)
        victron_metrics_obj.inverter_state.set.assert_called()  # type: ignore[attr-defined]

    def test_update_from_dbus_cell_voltage(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test cell voltage metric update."""
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.battery.ttyO1", "/Dc/0/Voltages/Cell1", 3.45
        )
        victron_metrics_obj.cell_voltage.set.assert_called()  # type: ignore[attr-defined]

    def test_update_from_dbus_cell_temperature(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test cell temperature metric update."""
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.battery.ttyO1", "/Temperatures/Cell1", 25.0
        )
        victron_metrics_obj.cell_temperature.set.assert_called()  # type: ignore[attr-defined]

    def test_extract_serial(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test serial extraction from service name."""
        assert victron_metrics_obj._extract_serial("com.victronenergy.battery.ttyO1") == "ttyO1"
        assert victron_metrics_obj._extract_serial("com.victronenergy.vebus.ttyO2") == "ttyO2"
        assert (
            victron_metrics_obj._extract_serial("com.victronenergy.solarcharger.ttyO0") == "ttyO0"
        )

    def test_update_ignores_invalid_values(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test that non-numeric values are ignored."""
        victron_metrics_obj.update_from_dbus("com.victronenergy.battery.ttyO1", "/Soc", "invalid")
        victron_metrics_obj.battery_soc.set.assert_not_called()  # type: ignore[attr-defined]

    def test_update_with_attributes(self, victron_metrics_obj: VictronMetrics) -> None:
        """Test updating with additional attributes."""
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.battery.ttyO1", "/Soc", 50.0, {"location": "home"}
        )
        # Attributes should be passed to gauge
        victron_metrics_obj.battery_soc.set.assert_called_once()  # type: ignore[attr-defined]
        args, kwargs = victron_metrics_obj.battery_soc.set.call_args  # type: ignore[attr-defined]
        # gauge.set(value, attributes) - attributes is 2nd positional arg
        assert args[1].get("location") == "home"
        assert args[1].get("serial") == "ttyO1"


class TestRealVenusPaths:
    """Tests for mappings of paths actually emitted by Venus OS."""

    def test_otelside_yield_power_maps_to_pv(self, victron_metrics_obj: VictronMetrics) -> None:
        """/Yield/Power from solarcharger services must feed victron_pv_power."""
        victron_metrics_obj.update_from_dbus(
            "com.victronenergy.solarcharger.ttyUSB3", "/Yield/Power", 1048.0
        )
        victron_metrics_obj.pv_power.set.assert_called_once()  # type: ignore[attr-defined]

    def test_prometheus_yield_power_maps_to_pv(self) -> None:
        """/Yield/Power must populate the victron_pv_power gauge."""
        from venus_observability.metrics import pv_power, update_prometheus_from_dbus

        update_prometheus_from_dbus(
            "com.victronenergy.solarcharger.ttyUSB3", "/Yield/Power", 1200.0
        )
        value = pv_power.labels(serial="ttyUSB3")._value.get()  # type: ignore[attr-defined]
        assert value == 1200.0

    def test_prometheus_per_phase_grid_and_consumption(self) -> None:
        """system service emits /Ac/Grid/L1/Power and /Ac/Consumption/L1/Power."""
        from venus_observability.metrics import (
            ac_loads,
            grid_power,
            update_prometheus_from_dbus,
        )

        update_prometheus_from_dbus("com.victronenergy.system", "/Ac/Grid/L1/Power", 128.0)
        update_prometheus_from_dbus("com.victronenergy.system", "/Ac/Grid/L2/Power", 7.0)
        update_prometheus_from_dbus("com.victronenergy.system", "/Ac/Consumption/L1/Power", 577.0)
        grid_l1 = grid_power.labels(  # type: ignore[attr-defined]
            serial="system", phase="l1"
        )._value.get()
        grid_l2 = grid_power.labels(  # type: ignore[attr-defined]
            serial="system", phase="l2"
        )._value.get()
        loads_l1 = ac_loads.labels(  # type: ignore[attr-defined]
            serial="system", phase="l1"
        )._value.get()
        assert grid_l1 == 128.0
        assert grid_l2 == 7.0
        assert loads_l1 == 577.0
