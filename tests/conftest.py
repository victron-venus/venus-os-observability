"""Pytest configuration and fixtures."""

import sys
from unittest.mock import MagicMock

import pytest
from opentelemetry.metrics import Meter

# Mock D-Bus and GLib modules that aren't available on macOS
# Must be done BEFORE any imports that might trigger gi
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()

# pylint: disable=wrong-import-position
from venus_observability.metrics import VictronMetrics  # noqa: E402


@pytest.fixture
def mock_meter() -> MagicMock:
    """Create a mock OpenTelemetry Meter."""
    meter = MagicMock(spec=Meter)
    # Mock gauge creation
    gauge = MagicMock()
    meter.create_gauge.return_value = gauge
    return meter


@pytest.fixture
def victron_metrics(meter: MagicMock) -> VictronMetrics:
    """Create VictronMetrics instance with mock meter."""
    return VictronMetrics(meter)
