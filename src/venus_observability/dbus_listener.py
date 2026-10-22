"""
D-Bus signal listener for Victron devices with OpenTelemetry tracing.
"""

# Lazy annotations: Venus OS ships a stripped dbus-python without dbus.Message
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from .correlation import (
    CorrelationContext,
    extract_correlation_id_from_headers,
    get_correlation_id,
)
from .dbus_owners import BUS_ITEM_INTERFACE, ServiceOwnerTracker
from .metrics import VictronMetrics, invalidate_prometheus_service, update_prometheus_from_dbus

# Initialize D-Bus main loop
DBusGMainLoop(set_as_default=True)

# Common D-Bus paths used across multiple service types
DC_VOLTAGE_PATH = "/Dc/0/Voltage"
STOP_TIMEOUT_SECONDS = 5.0
STOP_POLL_SECONDS = 0.05


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
        self._loop_thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._stop_requested = False
        self._subscriptions: set[tuple[str, str, str]] = set()
        self._global_subscription = False
        self._match_rules: list[str] = []
        self._filter_registered = False
        self._owners = ServiceOwnerTracker(
            self.bus, self._process_values, self._invalidate_service, self._select_service
        )

    def _invalidate_service(self, service: str) -> None:
        self.metrics.invalidate_service(service)
        invalidate_prometheus_service(service)

    def _install_filter(self) -> None:
        if not self._filter_registered:
            self.bus.add_message_filter(self._message_filter)
            self._filter_registered = True
        self._owners.start()

    def subscribe_global(self) -> None:
        """Subscribe to all Victron BusItem ItemsChanged signals.

        Victron services emit ItemsChanged from the root object path ``/``
        with a dict of absolute item paths, so one global match rule covers
        every service (per-path rules never match).
        """
        if self._global_subscription:
            return
        try:
            match_rule = (
                "type='signal',interface='com.victronenergy.BusItem',member='ItemsChanged',path='/'"
            )
            self.bus.add_match_string(match_rule)
            self._install_filter()
            self._match_rules.append(match_rule)
            self._global_subscription = True
            self.logger.info("Subscribed to Victron ItemsChanged signals")
        except dbus.DBusException as e:
            self.logger.warning("Failed to subscribe to Victron signals: %s", e)

    def subscribe(
        self,
        service: str,
        path: str,
        interface: str = BUS_ITEM_INTERFACE,
    ) -> None:
        """Receive root batches, selecting this service/interface's exact item path."""
        key = (service, interface, path)
        if key in self._subscriptions:
            return

        try:
            match_rule = (
                f"type='signal',"
                f"sender='{service}',"
                "path='/',"
                f"interface='{interface}',"
                f"member='ItemsChanged'"
            )
            if match_rule not in self._match_rules:
                self.bus.add_match_string(match_rule)
                self._install_filter()
                self._match_rules.append(match_rule)
            self._subscriptions.add(key)
            self.logger.debug("Subscribed to %s%s", service, path)
        except dbus.DBusException as e:
            self.logger.warning("Failed to subscribe to %s%s: %s", service, path, e)

    def subscribe_service(self, service: str, paths: list[str]) -> None:
        """Subscribe to multiple paths on a service."""
        for path in paths:
            self.subscribe(service, path)

    def _extract_headers_from_message(self, message: dbus.Message) -> dict[str, str]:
        """Extract correlation headers from D-Bus message."""
        headers: dict[str, str] = {}
        # Try to get correlation ID from message metadata if available
        # D-Bus doesn't have standard headers, but we can check for custom metadata
        with suppress(Exception):
            # Check for any custom properties or annotations
            # (This is a placeholder - D-Bus typically doesn't carry correlation IDs natively)
            pass
        return headers

    def _message_filter(self, bus: dbus.Bus, message: dbus.Message) -> None:
        """Handle incoming D-Bus signal messages."""
        if (
            message.get_type() != 4  # D-Bus SIGNAL, not a method on a shared connection
            or message.get_member() != "ItemsChanged"
            or message.get_path() != "/"
        ):
            return

        try:
            interface = str(message.get_interface() or "")
            if not (
                (self._global_subscription and interface == BUS_ITEM_INTERFACE)
                or any(selected == interface for _, selected, _ in self._subscriptions)
            ):
                return
            raw_sender = str(message.get_sender() or "")
            args = message.get_args_list()

            if not args:
                return

            # ItemsChanged payload: dict of absolute item paths -> {Value, Text}
            changed = args[0] if isinstance(args[0], dict) else {}

            # Extract correlation ID from message if available
            headers = self._extract_headers_from_message(message)
            correlation_id = extract_correlation_id_from_headers(headers)

            self._owners.submit(raw_sender, dict(changed), correlation_id, interface)

        except Exception as e:
            self.logger.error("Error processing D-Bus signal: %s", e)

    def _select_service(self, names: set[str], interface: str) -> str | None:
        """Keep the stable global identity, or choose an explicitly subscribed alias."""
        if self._global_subscription and interface == BUS_ITEM_INTERFACE:
            return min(names) if names else None
        selected = {
            service
            for service, selected_interface, _ in self._subscriptions
            if service in names and selected_interface == interface
        }
        return min(selected) if selected else None

    def _process_values(
        self, service: str, changed: dict[str, Any], correlation_id: str | None, interface: str
    ) -> None:
        """Select immediate or replayed values under one stable service identity."""
        if not (self._global_subscription and interface == BUS_ITEM_INTERFACE):
            changed = {
                str(path): value
                for path, value in changed.items()
                if (service, interface, str(path)) in self._subscriptions
            }
        if not changed:
            return
        try:
            with self._trace_signal(service, "/", changed, correlation_id) as span:
                for key, value in changed.items():
                    self._handle_value(service, str(key), value, span)
        except Exception as error:
            self.logger.error("Error processing D-Bus signal: %s", error)

    @contextmanager
    def _trace_signal(
        self, service: str, path: str, data: dict[str, Any], correlation_id: str | None = None
    ) -> Iterator[Span]:
        """Create trace span for D-Bus signal processing with correlation ID."""
        span_name = f"dbus.signal.{service.replace('.', '_')}{path.replace('/', '_')}"

        # Use correlation ID from context or provided
        corr_id = correlation_id or get_correlation_id()

        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CONSUMER,
            attributes={
                "dbus.service": service,
                "dbus.path": path,
                "dbus.changed_keys": [str(key) for key in data],
                "correlation.id": corr_id or "",
            },
        ) as span:
            # Set correlation ID in context for downstream operations
            if corr_id:
                with CorrelationContext(corr_id):
                    try:
                        yield span
                    except Exception as e:
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        raise
            else:
                try:
                    yield span
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

    def _handle_value(self, service: str, path: str, value: Any, span: Span) -> None:
        """Process a single D-Bus value update."""
        start_time = time.perf_counter()

        # Add to span
        if span.is_recording():
            span.set_attribute(f"dbus.value.{path}", str(value))

        # Victron ItemsChanged values arrive as {Value: x, Text: "..."}
        raw = value
        if isinstance(raw, dict):
            if "Value" not in raw:
                return
            raw = raw["Value"]

        # Update OpenTelemetry metrics
        self.metrics.update_from_dbus(service, path, raw)

        # Update Prometheus metrics
        update_prometheus_from_dbus(service, path, raw)

        # Record latency
        latency_ms = (time.perf_counter() - start_time) * 1000
        span.set_attribute("dbus.processing_latency_ms", latency_ms)

    def start(self) -> None:
        """Start one owned background thread; a stopped listener cannot restart."""
        with self._lifecycle_lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._stop_requested:
            raise RuntimeError("D-Bus listener has been stopped")
        if self._loop_thread is not None:
            raise RuntimeError("D-Bus listener is already started")
        self.logger.info("Starting D-Bus event loop")
        thread = threading.Thread(target=self._run_loop, name="dbus-mainloop", daemon=True)
        self._loop_thread = thread
        try:
            thread.start()
        except Exception:
            self._loop_thread = None
            raise

    def _run_loop(self) -> None:
        """Create and run the GLib main loop (must own its thread)."""
        try:
            with self._lifecycle_lock:
                if self._stop_requested:
                    return
            loop = GLib.MainLoop()
            with self._lifecycle_lock:
                self._main_loop = loop
                if self._stop_requested:
                    return
            loop.run()
        except KeyboardInterrupt:
            self.logger.info("D-Bus event loop stopped")
        finally:
            with self._lifecycle_lock:
                self._main_loop = None

    def run(self) -> None:
        """Start or await the owned worker, without running GLib on a second thread."""
        with self._lifecycle_lock:
            if self._loop_thread is None:
                self._start_locked()
            thread = self._loop_thread
        assert thread is not None
        if thread is threading.current_thread():
            raise RuntimeError("D-Bus worker cannot wait for itself")
        try:
            thread.join()
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        """Request termination and wait at most five seconds outside the worker.

        Calls from a GLib callback only request exit: that thread cannot join
        itself. A timeout retains the thread handle so a later stop can join it.
        """
        self.logger.info("Stopping D-Bus event loop")
        with self._lifecycle_lock:
            cleanup_required = not self._stop_requested
            self._stop_requested = True
            thread = self._loop_thread
        try:
            self._stop_loop(thread)
        finally:
            if cleanup_required:
                self._remove_subscriptions()

    def _stop_loop(self, thread: threading.Thread | None) -> None:
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while True:
            with self._lifecycle_lock:
                loop = self._main_loop
            if loop is not None and loop.is_running():
                loop.quit()
            if thread is None or thread is threading.current_thread():
                return
            # GLib ignores quit before run starts. Repeat the request while
            # joining to cover that transition without holding a lifecycle lock
            # across GLib calls or waiting indefinitely for its constructor.
            thread.join(max(0, min(STOP_POLL_SECONDS, deadline - time.monotonic())))
            if not thread.is_alive():
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("D-Bus event loop did not stop within five seconds")

    def _remove_subscriptions(self) -> None:
        self._owners.stop()
        # Remove match rules
        for rule in self._match_rules:
            with suppress(dbus.DBusException):
                self.bus.remove_match_string(rule)
        self._match_rules.clear()
        self._subscriptions.clear()
        self._global_subscription = False
        if self._filter_registered:
            self.bus.remove_message_filter(self._message_filter)
            self._filter_registered = False


class VictronServiceDiscovery:
    """Discover Victron services on D-Bus."""

    KNOWN_SERVICES = {
        "battery": [
            "/Soc",
            DC_VOLTAGE_PATH,
            "/Dc/0/Current",
            "/Dc/0/Power",
            "/Dc/0/Temperature",
            "/CustomName",
        ],
        "solarcharger": [
            "/Yield/Power",
            DC_VOLTAGE_PATH,
            "/Dc/0/Current",
            "/Dc/0/Power",
        ],
        "vebus": [
            "/State",
            "/Ac/Out/L1/P",
            "/Ac/Grid/L1/P",
            DC_VOLTAGE_PATH,
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
        import xml.etree.ElementTree as ET  # nosec B405 - bounded text, entities rejected below

        paths = []
        try:
            obj = self.bus.get_object(service, "/")
            xml = obj.Introspect(dbus_interface="org.freedesktop.DBus.Introspectable")
            if not isinstance(xml, str) or len(xml) > 1024 * 1024 or "<!ENTITY" in xml.upper():
                return []
            # ElementTree does not fetch an external DTD. Preserve standard D-Bus
            # DOCTYPE declarations but reject internal entities before parsing.
            root = ET.fromstring(xml)  # nosec B314 - bounded text, no entity declarations
            for node in root.findall(".//node"):
                node_name = node.get("name")
                if node_name:
                    paths.append(f"/{node_name}")
        except (dbus.DBusException, ET.ParseError):
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
