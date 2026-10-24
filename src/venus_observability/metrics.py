"""
Prometheus metrics for Victron Venus OS observability.

Exports inverter state metrics: SOC, power, grid, battery, loads.
"""

import math
from collections.abc import Sequence
from threading import Lock
from typing import Any

from opentelemetry.metrics import CallbackOptions, Meter, Observation
from prometheus_client import Counter, Gauge, Histogram

# Shared protocol identifiers keep publication and update paths consistent.
POWER_SUFFIX = "/power"
CELL_VOLTAGE_SUFFIX = "/dc/0/voltages/cell"
CELL_TEMPERATURE_SUFFIX = "/temperatures/cell"


def _numeric_or_nan(value: Any) -> float:
    """Invalidate unavailable D-Bus values instead of retaining a stale gauge."""
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return math.nan
    return number if math.isfinite(number) else math.nan


# Prometheus metrics (for /metrics endpoint)
battery_soc = Gauge(
    "victron_battery_soc",
    "Battery state of charge percentage",
    ["serial"],
)

battery_power = Gauge(
    "victron_battery_power",
    "Battery charge/discharge power in watts (positive = charging)",
    ["serial"],
)

pv_power = Gauge(
    "victron_pv_power",
    "Total PV production in watts",
    ["serial"],
)

grid_power = Gauge(
    "victron_grid_power",
    "Grid import/export power in watts (positive = import)",
    ["serial", "phase"],
)

ac_loads = Gauge(
    "victron_ac_loads",
    "AC loads consumption in watts",
    ["serial", "phase"],
)

inverter_state = Gauge(
    "victron_inverter_state",
    "Inverter state machine: 0=off, 1=on, 2=charging, 3=inverting",
    ["serial"],
)

cell_voltages = Gauge(
    "victron_battery_cell_voltage",
    "Individual battery cell voltage",
    ["serial", "cell"],
)

cell_temperature = Gauge(
    "victron_battery_cell_temperature",
    "Individual battery cell temperature",
    ["serial", "cell"],
)

# D-Bus signal tracing
dbus_signals_received = Counter(
    "victron_dbus_signals_received_total",
    "Total D-Bus signals received",
    ["service", "path", "interface"],
)

dbus_signal_errors = Counter(
    "victron_dbus_signal_errors_total",
    "Total D-Bus signal processing errors",
    ["service", "path", "error_type"],
)

dbus_unresolved_signals_dropped = Counter(
    "victron_dbus_unresolved_signals_dropped_total",
    "Unresolved D-Bus batches discarded after queue overflow or expiry",
    ["reason"],
)

# MQTT metrics
mqtt_messages_received = Counter(
    "victron_mqtt_messages_received_total",
    "Total MQTT messages received",
    ["topic"],
)

mqtt_publish_duration = Histogram(
    "victron_mqtt_publish_duration_seconds",
    "MQTT publish duration in seconds",
    ["topic"],
)

# Correlation ID propagation
trace_propagation_errors = Counter(
    "victron_trace_propagation_errors_total",
    "Total trace context propagation errors",
    ["operation"],
)


class _ObservedGauge:
    """Export current finite samples; absent samples represent unavailable data.

    Recent OpenTelemetry SDKs reject NaN instead of replacing the previous
    synchronous gauge value. Observable gauges let us omit an unavailable series
    without publishing stale data or a fabricated zero. Direct Prometheus gauges
    retain their existing NaN convention.
    """

    def __init__(self, meter: Meter, *, name: str, description: str, unit: str):
        self._samples: dict[tuple[tuple[str, Any], ...], Observation] = {}
        self._lock = Lock()
        self._instrument = meter.create_observable_gauge(
            name=name, description=description, unit=unit, callbacks=[self.observe]
        )

    def observe(self, _options: CallbackOptions) -> Sequence[Observation]:
        """Return an isolated snapshot for the SDK's collection thread."""
        with self._lock:
            return tuple(
                Observation(sample.value, dict(sample.attributes or {}))
                for sample in self._samples.values()
            )

    def set(self, value: float, attributes: dict[str, Any]) -> None:
        with self._lock:
            self._samples[tuple(sorted(attributes.items()))] = Observation(value, attributes)

    def remove(self, attributes: dict[str, Any]) -> None:
        with self._lock:
            self._samples.pop(tuple(sorted(attributes.items())), None)


class VictronMetrics:
    """Victron-specific metrics collector using OpenTelemetry Meter."""

    def __init__(self, meter: Meter):
        self.meter = meter
        # Retain only active series and their latest publisher. Service suffixes
        # can collide, so service loss must not invalidate another publisher.
        self._active_gauges: dict[
            tuple[int, tuple[tuple[str, Any], ...]], tuple[str, _ObservedGauge, dict[str, Any]]
        ] = {}

        self._series_lock = Lock()

        # Create instruments
        self.battery_soc = _ObservedGauge(
            meter,
            name="victron.battery.soc",
            description="Battery state of charge percentage",
            unit="%",
        )

        self.battery_power = _ObservedGauge(
            meter,
            name="victron.battery.power",
            description="Battery charge/discharge power",
            unit="W",
        )

        self.pv_power = _ObservedGauge(
            meter,
            name="victron.pv.power",
            description="Total PV production",
            unit="W",
        )

        self.grid_power = _ObservedGauge(
            meter,
            name="victron.grid.power",
            description="Grid import/export power",
            unit="W",
        )

        self.ac_loads = _ObservedGauge(
            meter,
            name="victron.ac.loads",
            description="AC loads consumption",
            unit="W",
        )

        self.inverter_state = _ObservedGauge(
            meter,
            name="victron.inverter.state",
            description="Inverter state machine",
            unit="1",
        )

        self.cell_voltage = _ObservedGauge(
            meter,
            name="victron.battery.cell.voltage",
            description="Individual cell voltage",
            unit="V",
        )

        self.cell_temperature = _ObservedGauge(
            meter,
            name="victron.battery.cell.temperature",
            description="Individual cell temperature",
            unit="Cel",
        )

    def update_from_dbus(
        self,
        service: str,
        path: str,
        value: Any,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Update metrics from D-Bus signal.

        Args:
            service: D-Bus service name (e.g., com.victronenergy.battery.ttyO1)
            path: D-Bus object path (e.g., /Soc)
            value: Signal value
            attributes: Additional attributes (serial, etc.)
        """
        attrs = dict(attributes or {})

        # Extract serial from service name
        serial = self._extract_serial(service)
        if serial:
            attrs["serial"] = serial

        # Map D-Bus paths to metrics
        path_lower = path.lower()

        if path_lower == "/soc":
            self._set_gauge(self.battery_soc, value, attrs, service)
        elif path_lower == "/dc/0/power":
            self._set_gauge(self.battery_power, value, attrs, service)
        elif path_lower.startswith(CELL_VOLTAGE_SUFFIX):
            cell_num = path_lower.replace(CELL_VOLTAGE_SUFFIX, "")
            if cell_num.isdigit():
                attrs["cell"] = cell_num
                self._set_gauge(self.cell_voltage, value, attrs, service)
        elif path_lower.startswith(CELL_TEMPERATURE_SUFFIX):
            cell_num = path_lower.replace(CELL_TEMPERATURE_SUFFIX, "")
            if cell_num.isdigit():
                attrs["cell"] = cell_num
                self._set_gauge(self.cell_temperature, value, attrs, service)
        elif _is_pv_power(service, path_lower):
            self._set_gauge(self.pv_power, value, attrs, service)
        elif path_lower == "/ac/grid/power" or self._is_phase_power(path_lower, "grid"):
            attrs = dict(attrs, phase=self._phase_from_path(path))
            self._set_gauge(self.grid_power, value, attrs, service)
        elif self._is_loads_power(path_lower):
            attrs = dict(attrs, phase=self._phase_from_path(path))
            self._set_gauge(self.ac_loads, value, attrs, service)
        elif path_lower == "/state":
            self._set_gauge(self.inverter_state, value, attrs, service)

    def _is_loads_power(self, path_lower: str) -> bool:
        """Match consumption paths while retaining overridable phase matching."""
        return (
            path_lower == "/ac/loads/power"
            or self._is_phase_power(path_lower, "loads")
            or (path_lower.startswith("/ac/consumption/") and path_lower.endswith(POWER_SUFFIX))
        )

    @staticmethod
    def _phase_from_path(path: str) -> str:
        """Extract the phase component (e.g. 'l1') from /Ac/<X>/<L1>/Power."""
        parts = path.split("/")
        return parts[3].lower() if len(parts) >= 5 and parts[3].lower().startswith("l") else ""

    @staticmethod
    def _is_phase_power(path_lower: str, section: str) -> bool:
        """Match per-phase watt paths like /Ac/Grid/L1/Power."""
        return path_lower.startswith(f"/ac/{section}/") and path_lower.endswith(POWER_SUFFIX)

    def _set_gauge(
        self, gauge: _ObservedGauge, value: Any, attributes: dict[str, Any], service: str
    ) -> None:
        """Omit unavailable values, including Venus empty arrays, from OTel."""
        # OpenTelemetry accepts primitive attributes and sequences. Freeze
        # sequences so a caller cannot change a recorded series after delivery.
        attrs = {
            key: tuple(value) if isinstance(value, list | tuple) else value
            for key, value in attributes.items()
        }
        number = _numeric_or_nan(value)
        key = (id(gauge), tuple(sorted(attrs.items())))
        with self._series_lock:
            if math.isfinite(number):
                gauge.set(number, attrs)
                self._active_gauges[key] = (service, gauge, attrs)
            else:
                gauge.remove(attrs)
                self._active_gauges.pop(key, None)

    def invalidate_service(self, service: str) -> None:
        """Invalidate only existing series last published by the lost service."""
        with self._series_lock:
            for key, (publisher, gauge, attrs) in tuple(self._active_gauges.items()):
                if publisher == service:
                    gauge.remove(attrs)
                    del self._active_gauges[key]

    def _extract_serial(self, service: str) -> str:
        """Extract device serial from service name."""
        # com.victronenergy.battery.ttyO1 -> ttyO1
        # com.victronenergy.vebus.ttyO2 -> ttyO2
        parts = service.split(".")
        if len(parts) >= 4:
            return parts[-1]
        return service


# Prometheus-only metrics helpers (for direct /metrics endpoint)
_prometheus_publishers: dict[Gauge, str] = {}


def _set_prometheus_gauge(service: str, gauge: Gauge, value: Any, **labels: str) -> None:
    """Track the existing label child, not a new service-to-label mapping."""
    child = gauge.labels(**labels)
    child.set(_numeric_or_nan(value))
    _prometheus_publishers[child] = service


def invalidate_prometheus_service(service: str) -> None:
    """Set the lost publisher's gauges to NaN without creating any label children."""
    for child, publisher in tuple(_prometheus_publishers.items()):
        if publisher == service:
            child.set(math.nan)
            del _prometheus_publishers[child]


def _phase_from_path(path: str) -> str:
    """Extract the phase component (e.g. 'l1') from /Ac/<X>/<L1>/Power."""
    parts = path.split("/")
    return parts[3].lower() if len(parts) >= 5 and parts[3].lower().startswith("l") else ""


def _is_phase_power(path_lower: str, section: str) -> bool:
    """Match per-phase watt paths like /Ac/Grid/L1/Power."""
    return path_lower.startswith(f"/ac/{section}/") and path_lower.endswith(POWER_SUFFIX)


def _is_pv_power(service: str, path_lower: str) -> bool:
    """Recognize aggregate, charger and PV-inverter power paths."""
    return path_lower in ("/dc/pv/power", "/yield/power") or (
        path_lower == "/ac/power" and ".pvinverter." in service
    )


def _is_grid_power(path_lower: str) -> bool:
    """Recognize aggregate and per-phase grid power paths."""
    return path_lower == "/ac/grid/power" or _is_phase_power(path_lower, "grid")


def _is_loads_power(path_lower: str) -> bool:
    """Recognize aggregate and per-phase AC consumption paths."""
    return (
        path_lower in ("/ac/loads/power",)
        or _is_phase_power(path_lower, "loads")
        or (path_lower.startswith("/ac/consumption/") and path_lower.endswith(POWER_SUFFIX))
    )


def update_prometheus_from_dbus(
    service: str, path: str, value: Any, serial: str | None = None
) -> None:
    """Update Prometheus gauges directly from D-Bus signal."""
    if serial is None:
        serial = service.split(".")[-1] if "." in service else service

    path_lower = path.lower()

    if path_lower == "/soc":
        _set_prometheus_gauge(service, battery_soc, value, serial=serial)
    elif path_lower == "/dc/0/power":
        _set_prometheus_gauge(service, battery_power, value, serial=serial)
    elif _is_pv_power(service, path_lower):
        # system aggregates PV as /Dc/Pv/Power; solarcharger emits /Yield/Power;
        # dbus-pvinverter services (dbus-tasmota-pv) report PV as /Ac/Power
        _set_prometheus_gauge(service, pv_power, value, serial=serial)
    elif _is_grid_power(path_lower):
        # Venus OS emits per-phase paths (/Ac/Grid/L1/Power); aggregate has no phase
        _set_prometheus_gauge(
            service, grid_power, value, serial=serial, phase=_phase_from_path(path)
        )
    elif _is_loads_power(path_lower):
        # Per-phase consumption (/Ac/Consumption/L1/Power) or legacy /Ac/Loads/*
        _set_prometheus_gauge(service, ac_loads, value, serial=serial, phase=_phase_from_path(path))
    elif path_lower == "/state":
        _set_prometheus_gauge(service, inverter_state, value, serial=serial)
    elif path_lower.startswith(CELL_VOLTAGE_SUFFIX):
        cell = path_lower.replace(CELL_VOLTAGE_SUFFIX, "")
        if cell.isdigit():
            _set_prometheus_gauge(service, cell_voltages, value, serial=serial, cell=cell)
    elif path_lower.startswith(CELL_TEMPERATURE_SUFFIX):
        cell = path_lower.replace(CELL_TEMPERATURE_SUFFIX, "")
        if cell.isdigit():
            _set_prometheus_gauge(service, cell_temperature, value, serial=serial, cell=cell)

    # Track signal
    dbus_signals_received.labels(
        service=service,
        path=path,
        interface="",
    ).inc()
