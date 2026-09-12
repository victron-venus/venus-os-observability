"""Resolve stable Venus service identities without blocking the D-Bus main loop."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from functools import partial
from typing import Any

from gi.repository import GLib

from .metrics import dbus_unresolved_signals_dropped

VENUS_PREFIX = "com.victronenergy."
BUS_DAEMON = "org.freedesktop.DBus"
BUS_PATH = "/org/freedesktop/DBus"
MAX_PENDING_BATCHES = 256
MAX_PENDING_ITEMS = 4096
PENDING_TTL = 10.0
REFRESH_INTERVAL = 60.0


class ServiceOwnerTracker:
    """Keep current owners and a bounded queue until their stable names are known."""

    def __init__(
        self,
        bus: Any,
        deliver: Callable[[str, dict[str, Any], str | None], None],
        invalidate: Callable[[str], None],
    ) -> None:
        self.bus = bus
        self.deliver = deliver
        self.invalidate = invalidate
        self.logger = logging.getLogger(__name__)
        self.services: dict[str, str] = {}
        self.owners: dict[str, set[str]] = {}
        self.pending: deque[tuple[float, str, dict[str, Any], str | None]] = deque()
        self.pending_items = 0
        self._match: Any = None
        self._timer: Any = None
        self._closed = False
        self._epoch = 0
        self._requests: dict[object, Any] = {}
        self._refreshing = False
        self._changed: set[str] = set()
        self._last_refresh = -REFRESH_INTERVAL
        self._last_warning = -REFRESH_INTERVAL

    def start(self) -> None:
        """Subscribe before the asynchronous initial snapshot to cover startup races."""
        if self._match is not None or self._closed:
            return
        self._match = self.bus.add_signal_receiver(
            self._name_owner_changed,
            signal_name="NameOwnerChanged",
            dbus_interface=BUS_DAEMON,
            bus_name=BUS_DAEMON,
            path=BUS_PATH,
        )
        self._timer = GLib.timeout_add_seconds(1, self._tick)
        self._refresh()

    def submit(self, owner: str, changed: dict[str, Any], correlation_id: str | None) -> None:
        """Deliver known signals immediately; never use unique names as metric labels."""
        if self._closed or not changed:
            return
        if owner.startswith(VENUS_PREFIX):
            self.deliver(owner, changed, correlation_id)
            return
        if names := self.owners.get(owner):
            self.deliver(min(names), changed, correlation_id)
            return
        if not owner.startswith(":"):
            return
        self.start()
        self._expire()
        if len(changed) > MAX_PENDING_ITEMS:
            self._drop("overflow")
            return
        while self.pending and (
            len(self.pending) >= MAX_PENDING_BATCHES
            or self.pending_items + len(changed) > MAX_PENDING_ITEMS
        ):
            self.pending_items -= len(self.pending.popleft()[2])
            self._drop("overflow")
        self.pending.append((time.monotonic(), owner, changed, correlation_id))
        self.pending_items += len(changed)
        self._refresh()

    def _name_owner_changed(self, name: str, old_owner: str, new_owner: str) -> None:
        if self._closed or not str(name).startswith(VENUS_PREFIX):
            return
        name = str(name)
        if self._refreshing:
            self._changed.add(name)
        self._set_owner(name, str(new_owner))

    def _set_owner(self, name: str, owner: str) -> None:
        previous = self.services.pop(name, None)
        if previous is not None:
            if previous != owner:
                self.invalidate(name)
            names = self.owners[previous]
            names.discard(name)
            if not names:
                del self.owners[previous]
        if owner:
            self.services[name] = owner
            self.owners.setdefault(owner, set()).add(name)
            self._replay(owner)

    def _replay(self, owner: str) -> None:
        self._expire()
        replay = [item for item in self.pending if item[1] == owner]
        self.pending = deque(item for item in self.pending if item[1] != owner)
        self.pending_items -= sum(len(item[2]) for item in replay)
        for _, _, changed, correlation_id in replay:
            self.deliver(min(self.owners[owner]), changed, correlation_id)

    def _call(self, method: str, args: tuple[str, ...], reply: Callable[..., None]) -> None:
        """Use the connection API directly: no blocking proxy introspection/owner lookup."""
        token = object()
        epoch = self._epoch
        self._requests[token] = None

        def complete(value: Any = None, error: Any = None) -> None:
            if self._closed or epoch != self._epoch:
                return
            self._requests.pop(token, None)
            if error is None:
                reply(value)
            else:
                # Recover a transient bootstrap failure while queued batches are still fresh.
                self._last_refresh = time.monotonic() - REFRESH_INTERVAL + 5.0
            if not self._requests:
                self._refreshing = False
                self._changed.clear()

        try:
            request = self.bus.call_async(
                BUS_DAEMON,
                BUS_PATH,
                BUS_DAEMON,
                method,
                "s" if args else "",
                args,
                reply_handler=complete,
                error_handler=lambda error: complete(error=error),
                timeout=5.0,
            )
            if token in self._requests:
                self._requests[token] = request
        except Exception as error:
            complete(error=error)

    def _refresh(self) -> None:
        now = time.monotonic()
        if self._closed or self._refreshing or now - self._last_refresh < REFRESH_INTERVAL:
            return
        self._refreshing = True
        self._last_refresh = now
        self._changed.clear()
        self._call("ListNames", (), self._listed_names)

    def _listed_names(self, names: Any) -> None:
        present = {str(name) for name in names if str(name).startswith(VENUS_PREFIX)}
        for name in self.services.keys() - present - self._changed:
            self._set_owner(name, "")
        for name in sorted(present - self._changed):
            self._call("GetNameOwner", (name,), partial(self._found_owner, name))

    def _found_owner(self, name: str, owner: Any) -> None:
        # A lifecycle signal after ListNames takes precedence over an older async reply.
        if name not in self._changed:
            self._set_owner(name, str(owner))

    def _drop(self, reason: str) -> None:
        dbus_unresolved_signals_dropped.labels(reason=reason).inc()
        now = time.monotonic()
        if now - self._last_warning >= REFRESH_INTERVAL:
            self.logger.warning(
                "Discarded unresolved D-Bus batch (%s); pending queue is bounded", reason
            )
            self._last_warning = now

    def _expire(self) -> None:
        cutoff = time.monotonic() - PENDING_TTL
        while self.pending and self.pending[0][0] <= cutoff:
            self.pending_items -= len(self.pending.popleft()[2])
            self._drop("expired")

    def _tick(self) -> bool:
        if self._closed:
            return False
        self._expire()
        if self.pending:
            self._refresh()
        return True

    def stop(self) -> None:
        """Cancel outstanding lookups and release every owner, queue and GLib source."""
        self._closed = True
        self._epoch += 1
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        if self._match is not None:
            self._match.remove()
            self._match = None
        for request in self._requests.values():
            if request is not None:
                request.cancel()
        self._requests.clear()
        self._changed.clear()
        self.services.clear()
        self.owners.clear()
        self.pending.clear()
        self.pending_items = 0
