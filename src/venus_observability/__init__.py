"""
Venus OS Observability - OpenTelemetry/Prometheus for Victron ecosystem.

Provides D-Bus event tracing, inverter metrics export, and distributed tracing
across MQTT → D-Bus → inverter-control pipeline.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .__main__ import (
        ObservabilityService,
        mqtt_callback_with_correlation_extraction,
        mqtt_publish_with_correlation,
        setup_mqtt_correlation,
        setup_telemetry,
    )

__version__ = "0.1.4"
__author__ = "Victron Venus Team"
__license__ = "MIT"

__all__ = [
    "ObservabilityService",
    "setup_telemetry",
    "setup_mqtt_correlation",
    "mqtt_callback_with_correlation_extraction",
    "mqtt_publish_with_correlation",
]


def __getattr__(name: str) -> Any:
    """Load public helpers on demand without importing the CLI during python -m."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".__main__", __name__), name)
    globals()[name] = value
    return value
