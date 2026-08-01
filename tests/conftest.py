"""Pytest configuration and fixtures."""

import sys
from unittest.mock import MagicMock

import pytest
from opentelemetry.metrics import Meter

# Mock D-Bus and GLib modules that aren't available on macOS
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()


@pytest.fixture
def mock_meter() -> MagicMock:
    """Create a mock OpenTelemetry Meter."""
    meter = MagicMock(spec=Meter)
    # Mock gauge creation
    gauge = MagicMock()
    meter.create_gauge.return_value = gauge
    return meter


@pytest.fixture
def victron_metrics(mock_meter: MagicMock):
    """Create VictronMetrics instance with mock meter."""
    from venus_observability.metrics import VictronMetrics

    return VictronMetrics(mock_meter)

