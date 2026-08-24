"""
Prometheus metrics for Victron Venus OS observability.

Exports inverter state metrics: SOC, power, grid, battery, loads.
"""

from typing import Any

from opentelemetry.metrics import Meter
from prometheus_client import Counter, Gauge, Histogram

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


class VictronMetrics:
    """Victron-specific metrics collector using OpenTelemetry Meter."""

    def __init__(self, meter: Meter):
        self.meter = meter

        # Create instruments
        self.battery_soc = meter.create_gauge(
            name="victron.battery.soc",
            description="Battery state of charge percentage",
            unit="%",
        )

        self.battery_power = meter.create_gauge(
            name="victron.battery.power",
            description="Battery charge/discharge power",
            unit="W",
        )

        self.pv_power = meter.create_gauge(
            name="victron.pv.power",
            description="Total PV production",
            unit="W",
        )

        self.grid_power = meter.create_gauge(
            name="victron.grid.power",
            description="Grid import/export power",
            unit="W",
        )

        self.ac_loads = meter.create_gauge(
            name="victron.ac.loads",
            description="AC loads consumption",
            unit="W",
        )

        self.inverter_state = meter.create_gauge(
            name="victron.inverter.state",
            description="Inverter state machine",
            unit="1",
        )

        self.cell_voltage = meter.create_gauge(
            name="victron.battery.cell.voltage",
            description="Individual cell voltage",
            unit="V",
        )

        self.cell_temperature = meter.create_gauge(
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
        attrs = attributes or {}

        # Extract serial from service name
        serial = self._extract_serial(service)
        if serial:
            attrs["serial"] = serial

        # Map D-Bus paths to metrics
        path_lower = path.lower()

        if path_lower == "/soc":
            self._set_gauge(self.battery_soc, value, attrs)
        elif path_lower == "/dc/0/power":
            self._set_gauge(self.battery_power, value, attrs)
        elif path_lower.startswith("/dc/0/voltages/cell"):
            cell_num = path_lower.replace("/dc/0/voltages/cell", "")
            if cell_num.isdigit():
                attrs["cell"] = cell_num
                self._set_gauge(self.cell_voltage, value, attrs)
        elif path_lower.startswith("/temperatures/cell"):
            cell_num = path_lower.replace("/temperatures/cell", "")
            if cell_num.isdigit():
                attrs["cell"] = cell_num
                self._set_gauge(self.cell_temperature, value, attrs)
        elif path_lower in ("/dc/pv/power", "/yield/power") or (
            path_lower == "/ac/power" and ".pvinverter." in service
        ):
            self._set_gauge(self.pv_power, value, attrs)
        elif path_lower == "/ac/grid/power" or self._is_phase_power(path_lower, "grid"):
            attrs = dict(attrs, phase=self._phase_from_path(path))
            self._set_gauge(self.grid_power, value, attrs)
        elif (
            path_lower == "/ac/loads/power"
            or self._is_phase_power(path_lower, "loads")
            or (path_lower.startswith("/ac/consumption/") and path_lower.endswith("/power"))
        ):
            attrs = dict(attrs, phase=self._phase_from_path(path))
            self._set_gauge(self.ac_loads, value, attrs)
        elif path_lower == "/state":
            self._set_gauge(self.inverter_state, value, attrs)

    @staticmethod
    def _phase_from_path(path: str) -> str:
        """Extract the phase component (e.g. 'l1') from /Ac/<X>/<L1>/Power."""
        parts = path.split("/")
        return parts[3].lower() if len(parts) >= 5 and parts[3].lower().startswith("l") else ""

    @staticmethod
    def _is_phase_power(path_lower: str, section: str) -> bool:
        """Match per-phase watt paths like /Ac/Grid/L1/Power."""
        return path_lower.startswith(f"/ac/{section}/") and path_lower.endswith("/power")

    def _set_gauge(self, gauge: Any, value: Any, attributes: dict[str, Any]) -> None:
        """Safely set gauge value."""
        try:
            num_value = float(value)
            gauge.set(num_value, attributes)
        except (ValueError, TypeError):
            pass

    def _extract_serial(self, service: str) -> str:
        """Extract device serial from service name."""
        # com.victronenergy.battery.ttyO1 -> ttyO1
        # com.victronenergy.vebus.ttyO2 -> ttyO2
        parts = service.split(".")
        if len(parts) >= 4:
            return parts[-1]
        return service


# Prometheus-only metrics helpers (for direct /metrics endpoint)
def _phase_from_path(path: str) -> str:
    """Extract the phase component (e.g. 'l1') from /Ac/<X>/<L1>/Power."""
    parts = path.split("/")
    return parts[3].lower() if len(parts) >= 5 and parts[3].lower().startswith("l") else ""


def _is_phase_power(path_lower: str, section: str) -> bool:
    """Match per-phase watt paths like /Ac/Grid/L1/Power."""
    return path_lower.startswith(f"/ac/{section}/") and path_lower.endswith("/power")


def update_prometheus_from_dbus(
    service: str, path: str, value: Any, serial: str | None = None
) -> None:
    """Update Prometheus gauges directly from D-Bus signal."""
    if serial is None:
        serial = service.split(".")[-1] if "." in service else service

    path_lower = path.lower()

    if path_lower == "/soc":
        battery_soc.labels(serial=serial).set(float(value))
    elif path_lower == "/dc/0/power":
        battery_power.labels(serial=serial).set(float(value))
    elif path_lower in ("/dc/pv/power", "/yield/power") or (
        path_lower == "/ac/power" and ".pvinverter." in service
    ):
        # system aggregates PV as /Dc/Pv/Power; solarcharger emits /Yield/Power;
        # dbus-pvinverter services (dbus-tasmota-pv) report PV as /Ac/Power
        pv_power.labels(serial=serial).set(float(value))
    elif path_lower == "/ac/grid/power" or _is_phase_power(path_lower, "grid"):
        # Venus OS emits per-phase paths (/Ac/Grid/L1/Power); aggregate has no phase
        grid_power.labels(serial=serial, phase=_phase_from_path(path)).set(float(value))
    elif (
        path_lower in ("/ac/loads/power",)
        or _is_phase_power(path_lower, "loads")
        or (path_lower.startswith("/ac/consumption/") and path_lower.endswith("/power"))
    ):
        # Per-phase consumption (/Ac/Consumption/L1/Power) or legacy /Ac/Loads/*
        ac_loads.labels(serial=serial, phase=_phase_from_path(path)).set(float(value))
    elif path_lower == "/state":
        inverter_state.labels(serial=serial).set(int(value))
    elif path_lower.startswith("/dc/0/voltages/cell"):
        cell = path_lower.replace("/dc/0/voltages/cell", "")
        if cell.isdigit():
            cell_voltages.labels(serial=serial, cell=cell).set(float(value))
    elif path_lower.startswith("/temperatures/cell"):
        cell = path_lower.replace("/temperatures/cell", "")
        if cell.isdigit():
            cell_temperature.labels(serial=serial, cell=cell).set(float(value))

    # Track signal
    dbus_signals_received.labels(
        service=service,
        path=path,
        interface="",
    ).inc()
