"""Pytest configuration and fixtures."""

import pytest
from unittest.mock import MagicMock

from opentelemetry.metrics import Meter


@pytest.fixture
def mock_meter() -> MagicMock:
    """Create a mock OpenTelemetry Meter."""
    meter = MagicMock(spec=Meter)
    # Mock gauge creation
    gauge = MagicMock()
    meter.create_gauge.return_value = gauge
    return meter


@pytest.fixture
def victron_metrics(mock_meter) -> "VictronMetrics":
    """Create VictronMetrics instance with mock meter."""
    from venus_observability.metrics import VictronMetrics
    return VictronMetrics(mock_meter)