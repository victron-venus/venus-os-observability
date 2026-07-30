"""Pytest configuration and fixtures."""

from unittest.mock import MagicMock

import pytest
from opentelemetry.metrics import Meter

from venus_observability.metrics import VictronMetrics


@pytest.fixture
def mock_meter() -> MagicMock:
    """Create a mock OpenTelemetry Meter."""
    meter = MagicMock(spec=Meter)
    # Mock gauge creation
    gauge = MagicMock()
    meter.create_gauge.return_value = gauge
    return meter


@pytest.fixture
def victron_metrics(mock_meter: MagicMock) -> VictronMetrics:
    """Create VictronMetrics instance with mock meter."""
    return VictronMetrics(mock_meter)
