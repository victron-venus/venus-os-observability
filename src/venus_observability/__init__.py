"""
Venus OS Observability - OpenTelemetry/Prometheus for Victron ecosystem.

Provides D-Bus event tracing, inverter metrics export, and distributed tracing
across MQTT → D-Bus → inverter-control pipeline.
"""

from .__main__ import (
    ObservabilityService,
    mqtt_callback_with_correlation_extraction,
    mqtt_publish_with_correlation,
    setup_mqtt_correlation,
    setup_telemetry,
)

__version__ = "0.1.0"
__author__ = "Victron Venus Team"
__license__ = "MIT"

__all__ = [
    "ObservabilityService",
    "setup_telemetry",
    "setup_mqtt_correlation",
    "mqtt_callback_with_correlation_extraction",
    "mqtt_publish_with_correlation",
]

