"""Desktop session signals: screen lock and system suspend.

Lockly listens on D-Bus for:

* ``org.freedesktop.ScreenSaver.ActiveChanged`` (KDE, many desktops) and
  ``org.gnome.ScreenSaver.ActiveChanged`` (GNOME) on the session bus;
* ``org.freedesktop.login1.Session.Lock`` and
  ``org.freedesktop.login1.Manager.PrepareForSleep`` on the system bus.

Any of these triggers the callback. If a bus is unavailable (e.g. inside a
restrictive sandbox) the monitor silently does nothing; the inactivity timer
still applies.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)


class SessionMonitor:
    def __init__(self, on_lock: Callable[[str], None]):
        self._on_lock = on_lock
        self._subscriptions: list[tuple[Gio.DBusConnection, int]] = []
        self._connect()

    def _subscribe(self, bus_type, interface: str, member: str, handler) -> None:
        try:
            bus = Gio.bus_get_sync(bus_type, None)
        except GLib.Error:
            log.debug("D-Bus %s unavailable; %s.%s not monitored", bus_type, interface, member)
            return
        sub_id = bus.signal_subscribe(
            None, interface, member, None, None, Gio.DBusSignalFlags.NONE, handler
        )
        self._subscriptions.append((bus, sub_id))

    def _connect(self) -> None:
        def screensaver(_conn, _sender, _path, _iface, _member, params):
            if params.unpack()[0]:
                self._on_lock("screen-locked")

        def session_lock(_conn, _sender, _path, _iface, _member, _params):
            self._on_lock("session-locked")

        def sleep(_conn, _sender, _path, _iface, _member, params):
            if params.unpack()[0]:
                self._on_lock("suspend")

        self._subscribe(Gio.BusType.SESSION, "org.freedesktop.ScreenSaver", "ActiveChanged", screensaver)
        self._subscribe(Gio.BusType.SESSION, "org.gnome.ScreenSaver", "ActiveChanged", screensaver)
        self._subscribe(Gio.BusType.SYSTEM, "org.freedesktop.login1.Session", "Lock", session_lock)
        self._subscribe(Gio.BusType.SYSTEM, "org.freedesktop.login1.Manager", "PrepareForSleep", sleep)

    def close(self) -> None:
        for bus, sub_id in self._subscriptions:
            bus.signal_unsubscribe(sub_id)
        self._subscriptions.clear()
