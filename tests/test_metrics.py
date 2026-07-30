"""
Test configuration and fixtures for venus-os-observability.
"""

from unittest.mock import MagicMock

import pytest

from venus_observability.metrics import VictronMetrics


@pytest.fixture
def mock_meter():
    """Create a mock OpenTelemetry meter."""
    meter = MagicMock()
    gauge = MagicMock()
    meter.create_gauge.return_value = gauge
    return meter


@pytest.fixture
def victron_metrics(mock_meter):
    """Create VictronMetrics with mock meter."""
    return VictronMetrics(mock_meter)


class TestVictronMetrics:
    """Tests for VictronMetrics class."""

    def test_init_creates_all_gauges(self, mock_meter, victron_metrics):
        """Verify all expected gauges are created."""
        assert mock_meter.create_gauge.call_count == 8

    def test_update_from_dbus_soc(self, victron_metrics):
        """Test SOC metric update."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.battery.ttyO1",
            "/Soc",
            85.5
        )
        victron_metrics.battery_soc.set.assert_called()

    def test_update_from_dbus_battery_power(self, victron_metrics):
        """Test battery power metric update."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.battery.ttyO1",
            "/Dc/0/Power",
            -1200.0
        )
        victron_metrics.battery_power.set.assert_called()

    def test_update_from_dbus_pv_power(self, victron_metrics):
        """Test PV power metric update."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.solarcharger.ttyO0",
            "/Dc/Pv/Power",
            3500.0
        )
        victron_metrics.pv_power.set.assert_called()

    def test_update_from_dbus_grid_power(self, victron_metrics):
        """Test grid power metric update."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.grid.ttyO3",
            "/Ac/Grid/Power",
            -500.0
        )
        victron_metrics.grid_power.set.assert_called()

    def test_update_from_dbus_ac_loads(self, victron_metrics):
        """Test AC loads metric update."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.vebus.ttyO2",
            "/Ac/Loads/Power",
            2000.0
        )
        victron_metrics.ac_loads.set.assert_called()

    def test_update_from_dbus_inverter_state(self, victron_metrics):
        """Test inverter state metric update."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.vebus.ttyO2",
            "/State",
            3
        )
        victron_metrics.inverter_state.set.assert_called()

    def test_update_from_dbus_cell_voltage(self, victron_metrics):
        """Test cell voltage metric update."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.battery.ttyO1",
            "/Dc/0/Voltages/Cell1",
            3.45
        )
        victron_metrics.cell_voltage.set.assert_called()

    def test_update_from_dbus_cell_temperature(self, victron_metrics):
        """Test cell temperature metric update."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.battery.ttyO1",
            "/Temperatures/Cell1",
            25.0
        )
        victron_metrics.cell_temperature.set.assert_called()

    def test_extract_serial(self, victron_metrics):
        """Test serial extraction from service name."""
        assert victron_metrics._extract_serial("com.victronenergy.battery.ttyO1") == "ttyO1"
        assert victron_metrics._extract_serial("com.victronenergy.vebus.ttyO2") == "ttyO2"
        assert victron_metrics._extract_serial("com.victronenergy.solarcharger.ttyO0") == "ttyO0"

    def test_update_ignores_invalid_values(self, victron_metrics):
        """Test that non-numeric values are ignored."""
        victron_metrics.update_from_dbus("com.victronenergy.battery.ttyO1", "/Soc", "invalid")
        victron_metrics.battery_soc.set.assert_not_called()

    def test_update_with_attributes(self, victron_metrics):
        """Test updating with additional attributes."""
        victron_metrics.update_from_dbus(
            "com.victronenergy.battery.ttyO1",
            "/Soc",
            50.0,
            {"location": "home"}
        )
        # Attributes should be passed to gauge
        victron_metrics.battery_soc.set.assert_called_once()
        args, kwargs = victron_metrics.battery_soc.set.call_args
        # gauge.set(value, attributes) - attributes is 2nd positional arg
        assert args[1].get("location") == "home"
        assert args[1].get("serial") == "ttyO1"

