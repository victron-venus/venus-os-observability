"""
D-Bus signal listener for Victron devices with OpenTelemetry tracing.
"""

import logging
import time
from contextlib import contextmanager, suppress

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from .metrics import VictronMetrics, update_prometheus_from_dbus

# Initialize D-Bus main loop
DBusGMainLoop(set_as_default=True)


class DBusSignalListener:
    """Async D-Bus signal listener for Victron services."""

    def __init__(
        self,
        metrics: VictronMetrics,
        bus: dbus.Bus | None = None,
        tracer: trace.Tracer | None = None,
    ):
        self.metrics = metrics
        self.bus = bus or dbus.SystemBus()
        self.tracer = tracer or trace.get_tracer(__name__)
        self.logger = logging.getLogger(__name__)
        self._main_loop: GLib.MainLoop | None = None
        self._subscriptions: set[tuple[str, str]] = set()
        self._match_rules: list[str] = []

    def subscribe(
        self,
        service: str,
        path: str,
        interface: str = "com.victronenergy.BusItem",
    ) -> None:
        """Subscribe to D-Bus signals for a service/path."""
        key = (service, path)
        if key in self._subscriptions:
            return

        try:
            match_rule = (
                f"type='signal',"
                f"sender='{service}',"
                f"path='{path}',"
                f"interface='{interface}',"
                f"member='ItemsChanged'"
            )
            self.bus.add_match_string(match_rule)
            self.bus.add_message_filter(self._message_filter)
            self._subscriptions.add(key)
            self._match_rules.append(match_rule)
            self.logger.debug("Subscribed to %s%s", service, path)
        except dbus.DBusException as e:
            self.logger.warning("Failed to subscribe to %s%s: %s", service, path, e)

    def subscribe_service(self, service: str, paths: list[str]) -> None:
        """Subscribe to multiple paths on a service."""
        for path in paths:
            self.subscribe(service, path)

    def _message_filter(self, bus: dbus.Bus, message: dbus.Message) -> None:
        """Handle incoming D-Bus signal messages."""
        if message.get_member() != "ItemsChanged":
            return

        try:
            sender = message.get_sender()
            path = message.get_path()
            args = message.get_args_list()

            if not args:
                return

            # Args format: [changed_dict, removed_list]
            changed = args[0] if isinstance(args[0], dict) else {}
            removed = args[1] if isinstance(args[1], list) else []

            with self._trace_signal(sender, path, changed) as span:
                for key, value in changed.items():
                    full_path = f"{path}/{key}" if key else path
                    self._handle_value(sender, full_path, value, span)

                for key in removed:
                    self.logger.debug("D-Bus path removed: %s%s/%s", sender, path, key)

        except Exception as e:
            self.logger.error("Error processing D-Bus signal: %s", e)

    @contextmanager
    def _trace_signal(self, service: str, path: str, data: dict):
        """Create trace span for D-Bus signal processing."""
        span_name = f"dbus.signal.{service.replace('.', '_')}{path.replace('/', '_')}"
        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CONSUMER,
            attributes={
                "dbus.service": service,
                "dbus.path": path,
                "dbus.changed_keys": list(data.keys()),
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    def _handle_value(self, service: str, path: str, value, span: Span) -> None:
        """Process a single D-Bus value update."""
        start_time = time.perf_counter()

        # Add to span
        span.set_attribute(f"dbus.value.{path}", str(value))

        # Update OpenTelemetry metrics
        self.metrics.update_from_dbus(service, path, value)

        # Update Prometheus metrics
        update_prometheus_from_dbus(service, path, value)

        # Record latency
        latency_ms = (time.perf_counter() - start_time) * 1000
        span.set_attribute("dbus.processing_latency_ms", latency_ms)

    def start(self) -> None:
        """Start the D-Bus event loop in background."""
        self.logger.info("Starting D-Bus event loop")
        self._main_loop = GLib.MainLoop()

    def run(self) -> None:
        """Run the D-Bus event loop (blocking)."""
        if not self._main_loop:
            self.start()
        try:
            self._main_loop.run()
        except KeyboardInterrupt:
            self.logger.info("D-Bus event loop stopped")

    def stop(self) -> None:
        """Stop the D-Bus event loop."""
        self.logger.info("Stopping D-Bus event loop")
        if self._main_loop and self._main_loop.is_running():
            self._main_loop.quit()

        # Remove match rules
        for rule in self._match_rules:
            with suppress(dbus.DBusException):
                self.bus.remove_match_string(rule)
        self._match_rules.clear()
        self._subscriptions.clear()


class VictronServiceDiscovery:
    """Discover Victron services on D-Bus."""

    KNOWN_SERVICES = {
        "battery": [
            "/Soc",
            "/Dc/0/Voltage",
            "/Dc/0/Current",
            "/Dc/0/Power",
            "/Dc/0/Temperature",
            "/CustomName",
        ],
        "solarcharger": [
            "/Yield/Power",
            "/Dc/0/Voltage",
            "/Dc/0/Current",
            "/Dc/0/Power",
        ],
        "vebus": [
            "/State",
            "/Ac/Out/L1/P",
            "/Ac/Grid/L1/P",
            "/Dc/0/Voltage",
        ],
        "grid": [
            "/Ac/Power",
            "/Ac/L1/Power",
        ],
    }

    def __init__(self, bus: dbus.Bus | None = None):
        self.bus = bus or dbus.SystemBus()
        self.logger = logging.getLogger(__name__)

    def find_victron_services(self) -> dict[str, list[str]]:
        """Find all Victron services and their object paths."""
        services = {}

        try:
            obj = self.bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            iface = dbus.Interface(obj, "org.freedesktop.DBus")
            names = iface.ListNames()

            for name in names:
                if name.startswith("com.victronenergy."):
                    paths = self._introspect_service(name)
                    if paths:
                        services[name] = paths

        except dbus.DBusException as e:
            self.logger.error("Service discovery failed: %s", e)

        return services

    def _introspect_service(self, service: str) -> list[str]:
        """Introspect a service to find object paths."""
        paths = []
        try:
            obj = self.bus.get_object(service, "/")
            xml = obj.Introspect(dbus_interface="org.freedesktop.DBus.Introspectable")
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml)
            for node in root.findall(".//node"):
                node_name = node.get("name")
                if node_name:
                    paths.append(f"/{node_name}")
        except dbus.DBusException:
            pass
        return paths

    def get_known_paths(self, service: str) -> list[str]:
        """Get known paths for a service type."""
        for service_type, paths in self.KNOWN_SERVICES.items():
            if service_type in service.lower():
                return paths
        return []


async def create_listener(metrics: VictronMetrics) -> DBusSignalListener:
    """Create and configure a D-Bus listener for known Victron services."""
    listener = DBusSignalListener(metrics)
    discovery = VictronServiceDiscovery(listener.bus)

    # Discover and subscribe to services
    services = discovery.find_victron_services()
    for service, paths in services.items():
        known_paths = discovery.get_known_paths(service)
        if known_paths:
            listener.subscribe_service(service, known_paths)
        else:
            # Subscribe to all discovered paths
            for path in paths:
                listener.subscribe(service, path)

    return listener
