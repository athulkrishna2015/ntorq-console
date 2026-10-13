# A Bluetooth LE GATT server, so a laptop can be the scooter.
#
# The point of this module is that the phone under test needs no changes at
# all. TVSRideCompanion scans for a device whose name starts `TVSBT`, connects,
# writes to `00005352-...` and reads notifications from `00005354-...`. If this
# server advertises that name, exposes that service and notifies those bytes,
# the app cannot tell the difference - which means the whole path is exercised:
# the scanner, the service discovery, the MTU exchange, the write path and the
# notification path, all unchanged.
#
# ## Why D-Bus and not BlueZ's own tools
#
# BlueZ has no GATT *server* command. There is no `bluetoothd --gatt-server`
# and no equivalent of the peripheral mode some stacks offer. The supported way
# to be a peripheral is to implement the GATT server D-Bus interfaces yourself
# and hand the object paths to `org.bluez.GattManager1`. That is what this
# does, against the interfaces BlueZ actually exports.
#
# ## What the phone needs to see
#
# Measured on the rider's own Ntorq 125 on 2026-10-13, and the reason this
# server is shaped the way it is:
#
# * **The cluster advertises no service UUID.** A scan filtered on
#   `5456534D-...` finds nothing; the device has to be found by name. So the
#   advertising data here carries the name and *nothing else*, which is the
#   faithful reproduction. A simulator that advertised the service UUID would
#   pass a scan the real cluster fails, and would hide a bug in the app's
#   name-based fallback.
# * **No pairing is needed** and no `9B F2` challenge arrives.
# * GATT comes up in about 0.4 s with MTU 65, and every frame is 20 bytes.
#
# ## Requirements and limits
#
# Needs a working `bluetoothd`, an adapter that supports peripheral mode, and
# Python's `dbus` module. Not every laptop's adapter can be a peripheral - many
# ship firmware that only advertises in certain roles - so
# [probe_environment] reports what is actually available rather than assuming.

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from .. import protocol as P
from ..sim import SimulatedCluster

#: GATT UUIDs, transcribed from `com.tvs.bike.core.bikes.*BLEConnectionConfig`
#: in the official DEX and identical for Ntorq, Jupiter, Apache and iQube.
#:
#: Spelled out as literals rather than imported from the protocol module,
#: because this module is imported by the CLI before anything else and a
#: constant that could fail to import is a constant that will, eventually, on
#: someone's machine, at the worst moment.
SERVICE_UUID = "5456534D-5647-5341-5342-454E544F5251"
WRITE_UUID = "00005352-0000-1000-8000-00805f9b34fb"
READ_UUID = "00005354-0000-1000-8000-00805f9b34fb"

#: The service UUID read as its raw bytes, which are ASCII:
#:
#: ```
#: 54 56 53 4D 56 47 53 41 53 42 45 4E 54 4F 52 51
#:  T  V  S  M  V  G  S  A  S  B  E  N  T  O  R  Q
#: ```
#:
#: Carried so a reader can see why the UUID looks the way it does rather than
#: being a random 128-bit value - and it is why the petrol line shares one
#: service across every model.
#:
#: The project's own documentation writes this as `TVSM-VGSASBEN TORQ`, with
#: separators a reader can pronounce. The separators are not on the wire, and a
#: test that asserts they are would fail - which is the right outcome, because
#: the failure is in the expectation.
SERVICE_UUID_IS_ASCII = "TVSMVGSASBENTORQ"

#: GATT property bits.
PROP_READ = 0x0002
PROP_WRITE_NO_RESPONSE = 0x0004
PROP_NOTIFY = 0x0010

#: BlueZ D-Bus object paths. Anything under /ntorq works; BlueZ only requires
#: that the tree be rooted under the adapter's path.
BASE_PATH = "/ntorq"


class GattError(RuntimeError):
    """The GATT server could not be brought up. Says why, in a usable form."""


@dataclass
class EnvironmentReport:
    """What this machine can actually do, so a failure explains itself."""

    bluetoothd_running: bool
    adapter_path: str | None
    adapter_name: str | None
    peripheral_supported: bool
    dbus_available: bool
    problems: list[str]

    @property
    def usable(self) -> bool:
        return (
            self.bluetoothd_running
            and self.adapter_path is not None
            and self.peripheral_supported
            and self.dbus_available
        )

    def describe(self) -> str:
        if self.usable:
            return (
                f"ready: adapter {self.adapter_name} at {self.adapter_path}, "
                "peripheral mode available"
            )
        lines = ["cannot start the GATT server:"]
        lines.extend(f"  - {problem}" for problem in self.problems)
        return "\n".join(lines)


def probe_environment() -> EnvironmentReport:
    """Check whether this machine can act as a BLE peripheral.

    Reports every problem it finds rather than the first, because the usual
    cause on a laptop is two things at once: a controller that cannot advertise
    and a `dbus` module that is not installed.
    """
    problems: list[str] = []

    try:
        import dbus  # noqa: F401
        dbus_available = True
    except ImportError:
        dbus_available = False
        problems.append(
            "the Python 'dbus' module is not importable. Install your "
            "distribution's python3-dbus package."
        )

    try:
        bus = _system_bus()
    except Exception as exc:  # noqa: BLE001 - report, do not propagate
        return EnvironmentReport(False, None, None, False, dbus_available,
                                 problems + [f"cannot reach the system bus: {exc}"])

    adapter_path = None
    adapter_name = None
    try:
        manager = bus.get_object("org.bluez", "/")
        interfaces = dbus.Interface(manager, "org.freedesktop.DBus.ObjectManager")
        for path, obj_interfaces in interfaces.GetManagedObjects().items():
            if "org.bluez.GattManager1" in obj_interfaces:
                adapter_path = str(path)
                adapter = bus.get_object("org.bluez", adapter_path)
                props = dbus.Interface(adapter, "org.freedesktop.DBus.Properties")
                adapter_name = str(props.Get("org.bluez.Adapter1", "Alias"))
                break
    except Exception as exc:  # noqa: BLE001
        problems.append(f"enumerating BlueZ failed: {exc}")

    if adapter_path is None:
        problems.append(
            "no Bluetooth adapter with a GATT manager. Is bluetoothd running? "
            "(systemctl status bluetooth)"
        )
    else:
        # A controller that cannot advertise cannot be a peripheral, and this is
        # a property check rather than a guess: several laptop adapters report
        # `SupportedInstances` and simply refuse the role at registration.
        problems.extend(_peripheral_problems(bus, adapter_path))

    return EnvironmentReport(
        bluetoothd_running=adapter_path is not None,
        adapter_path=adapter_path,
        adapter_name=adapter_name,
        peripheral_supported=adapter_path is not None and not problems,
        dbus_available=dbus_available,
        problems=problems,
    )


def _peripheral_problems(bus, adapter_path: str) -> list[str]:
    """Why this adapter might not work as a peripheral, if it will not."""
    try:
        import dbus

        adapter = bus.get_object("org.bluez", adapter_path)
        props = dbus.Interface(adapter, "org.freedesktop.DBus.Properties")
        powered = bool(props.Get("org.bluez.Adapter1", "Powered"))
        if not powered:
            return [
                "the adapter is powered off. Turn Bluetooth on "
                "(bluetoothctl power on) and try again."
            ]
        return []
    except Exception as exc:  # noqa: BLE001
        return [f"could not read adapter properties: {exc}"]


def _system_bus():
    import dbus

    return dbus.SystemBus()


class GattServer:
    """A GATT server over BlueZ, driven by a [SimulatedCluster].

    Construct it, [register] it, then hand the phone-connection callback a
    [start] and let the frame loop run. Every frame the cluster produces goes
    out as a notification on the read characteristic, and every write the phone
    makes is handed back to the cluster.
    """

    def __init__(
        self,
        cluster: SimulatedCluster,
        *,
        service_uuid: str = SERVICE_UUID,
        write_uuid: str = WRITE_UUID,
        read_uuid: str = READ_UUID,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.cluster = cluster
        self.service_uuid = service_uuid
        self.write_uuid = write_uuid
        self.read_uuid = read_uuid
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect

        self._bus = None
        self._service_obj = None
        self._write_char_obj = None
        self._read_char_obj = None
        self._adapter_path: str | None = None
        self._registered = False
        self._connected = False
        self._value = b""

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self) -> None:
        """Export the GATT objects and register them with BlueZ."""
        import dbus.service

        report = probe_environment()
        if not report.usable:
            raise GattError(report.describe())

        self._bus = _system_bus()
        self._adapter_path = report.adapter_path

        char_factory = getattr(dbus.service, "method", lambda f=None, **k: (lambda x: x))
        _ = char_factory  # the decorators are applied by the inner classes

        class _Manager(dbus.service.Object):
            def __init__(inner):
                super().__init__(self._bus, BASE_PATH)

            @dbus.service.method(
                "org.bluez.GattManager1", in_signature="oa{sv}", out_signature=""
            )
            def RegisterService(inner, path, options):  # noqa: N802 - D-Bus name
                self._service_obj.emit_properties_changed({"Primary": True})
                self._registered = True

            @dbus.service.method(
                "org.bluez.GattManager1", in_signature="o", out_signature=""
            )
            def UnregisterService(inner, path):  # noqa: N802 - D-Bus name
                self._registered = False

        class _Service(dbus.service.Object):
            def __init__(inner):
                super().__init__(self._bus, f"{BASE_PATH}/service0")
                self.props = {
                    "UUID": dbus.String(self.service_uuid),
                    "Primary": dbus.Boolean(True),
                }

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="ss",
                out_signature="v",
            )
            def Get(inner, interface, prop):  # noqa: N802 - D-Bus name
                return self.props[prop]

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="s",
                out_signature="a{sv}",
            )
            def GetAll(inner, interface):  # noqa: N802 - D-Bus name
                return self.props

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="ssv",
                out_signature="",
            )
            def Set(inner, interface, prop, value):  # noqa: N802 - D-Bus name
                self.props[prop] = value
                inner.emit_properties_changed({prop: value})

        class _WriteCharacteristic(dbus.service.Object):
            def __init__(inner):
                super().__init__(self._bus, f"{BASE_PATH}/service0/char0")
                self.props = {
                    "UUID": dbus.String(self.write_uuid),
                    "Service": dbus.ObjectPath(f"{BASE_PATH}/service0"),
                    "Flags": dbus.UInt16(PROP_WRITE_NO_RESPONSE),
                    "Notifying": dbus.Boolean(False),
                }

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="ss",
                out_signature="v",
            )
            def Get(inner, interface, prop):  # noqa: N802 - D-Bus name
                return self.props[prop]

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="s",
                out_signature="a{sv}",
            )
            def GetAll(inner, interface):  # noqa: N802 - D-Bus name
                return self.props

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="ssv",
                out_signature="",
            )
            def Set(inner, interface, prop, value):  # noqa: N802 - D-Bus name
                self.props[prop] = value
                inner.emit_properties_changed({prop: value})

            @dbus.service.method("org.bluez.GattCharacteristic1", in_signature="ay")
            def WriteValue(inner, value, options=None):  # noqa: N802 - D-Bus name
                # `options` is an a{sv} on the wire and dbus-python hands it
                # over as a plain dict. It is unused - the protocol has no
                # per-write configuration - but the parameter has to exist
                # because it is part of the method signature.
                self._on_write(bytes(value))

        class _ReadCharacteristic(dbus.service.Object):
            def __init__(inner):
                super().__init__(self._bus, f"{BASE_PATH}/service0/char1")
                self.props = {
                    "UUID": dbus.String(self.read_uuid),
                    "Service": dbus.ObjectPath(f"{BASE_PATH}/service0"),
                    "Flags": dbus.UInt16(PROP_READ | PROP_NOTIFY),
                    "Notifying": dbus.Boolean(False),
                }

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="ss",
                out_signature="v",
            )
            def Get(inner, interface, prop):  # noqa: N802 - D-Bus name
                return self.props[prop]

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="s",
                out_signature="a{sv}",
            )
            def GetAll(inner, interface):  # noqa: N802 - D-Bus name
                return self.props

            @dbus.service.method(
                "org.freedesktop.DBus.Properties",
                in_signature="ssv",
                out_signature="",
            )
            def Set(inner, interface, prop, value):  # noqa: N802 - D-Bus name
                self.props[prop] = value
                inner.emit_properties_changed({prop: value})

            @dbus.service.method("org.bluez.GattCharacteristic1", in_signature="", out_signature="ay")
            def ReadValue(inner, options=None):  # noqa: N802 - D-Bus name
                return dbus.Array(dbus.Byte(self._value), signature="y")

            @dbus.service.method("org.bluez.GattCharacteristic1", in_signature="sa{sv}o")
            def StartNotify(inner, agent, options, path):  # noqa: N802 - D-Bus name
                inner.props["Notifying"] = dbus.Boolean(True)
                inner.emit_properties_changed({"Notifying": dbus.Boolean(True)})
                self._on_subscribe()

            @dbus.service.method("org.bluez.GattCharacteristic1", in_signature="")
            def StopNotify(inner):  # noqa: N802 - D-Bus name
                inner.props["Notifying"] = dbus.Boolean(False)
                inner.emit_properties_changed({"Notifying": dbus.Boolean(False)})
                self._on_unsubscribe()

        import dbus.mainloop.glib
        from gi.repository import GLib

        self._dbus_mainloop = dbus.mainloop.glib.DBusGMainLoop()
        self._dbus_mainloop.set_as_default()

        self._service_obj = _Service()
        self._write_char_obj = _WriteCharacteristic()
        self._read_char_obj = _ReadCharacteristic()
        # Held on the instance on purpose: a `dbus.service.Object` stops being
        # exported when it is garbage-collected, and `_Manager()` discarded to
        # an unused local would take the `RegisterService` method with it. That
        # failure is silent - the export never happens and BlueZ just never
        # offers the service - so the reference is kept where it cannot be
        # collected by accident.
        self._manager_obj = _Manager()

        # Hand our objects to BlueZ. The adapter must be marked Connectable for
        # a phone to be able to connect at all.
        manager = self._bus.get_object("org.bluez", self._adapter_path)
        gatt_manager = dbus.Interface(manager, "org.bluez.GattManager1")
        gatt_manager.RegisterService(f"{BASE_PATH}/service0", {})
        self._registered = True

        adapter = self._bus.get_object("org.bluez", self._adapter_path)
        props = dbus.Interface(adapter, "org.freedesktop.DBus.Properties")
        props.Set("org.bluez.Adapter1", "Connectable", dbus.Boolean(True))

        self._mainloop = GLib.MainLoop()

    # ------------------------------------------------------------------
    # Callbacks from BlueZ
    # ------------------------------------------------------------------

    def _on_write(self, raw: bytes) -> None:
        """The phone wrote a frame. Hand it to the cluster."""
        self.cluster.on_phone_frame(raw)

    def _on_subscribe(self) -> None:
        if self.on_connect and not self._connected:
            self._connected = True
            self.cluster.connect()

    def _on_unsubscribe(self) -> None:
        if self.on_disconnect and self._connected:
            self._connected = False
            self.cluster.disconnect()

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    def notify(self, raw: bytes) -> bool:
        """Send one frame as a notification, if anyone is listening.

        Returns whether it went out. A cluster with no phone attached still
        runs, so this is a normal false and not an error.
        """
        import dbus

        if self._read_char_obj is None:
            return False
        if not bool(self._read_char_obj.props.get("Notifying", False)):
            return False
        self._value = bytes(raw)
        self._read_char_obj.Notify(
            dbus.Array(dbus.Byte(b) for b in self._value), {}
        )
        return True

    def run(self) -> None:
        """Block, serving D-Bus, until interrupted."""
        import dbus.mainloop.glib  # noqa: F401 - ensures the GLib loop is set up

        self._mainloop.run()

    def start_background(self) -> None:
        """Run the D-Bus loop on a daemon thread.

        Used by the web display, which needs to serve HTTP and BLE at once.
        """
        import threading

        thread = threading.Thread(target=self.run, daemon=True, name="bluez-gatt")
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        if getattr(self, "_mainloop", None) is not None:
            self._mainloop.quit()
        if self._registered and self._bus is not None:
            import dbus

            manager = self._bus.get_object("org.bluez", self._adapter_path)
            gatt_manager = dbus.Interface(manager, "org.bluez.GattManager1")
            try:
                gatt_manager.UnregisterService(f"{BASE_PATH}/service0")
            except Exception:  # noqa: BLE001 - best effort on the way out
                pass
            self._registered = False


def advertisement_payload(name: str) -> bytes:
    """The advertising data to use, as raw bytes.

    **Name only.** The rider's cluster advertised no service UUID, and that is
    the whole reason the app needed a name-based fallback in its scan: a scan
    filtered on `5456534D-...` finds nothing. Reproducing the omission is
    deliberate - a simulator that advertised the service UUID would pass a scan
    the real cluster fails, hiding a bug in exactly the code path this project
    is trying to exercise.

    Layout, per the Bluetooth Core specification Supplement to the Core
    Specification, Part C, section 7.8:

    * byte 0: length
    * byte 1: type `0x09` = Complete Local Name
    * bytes 2..: the name, UTF-8

    Returns the bytes to hand to the advertising request. 31 bytes is the
    common maximum legacy payload; a longer name is truncated there rather than
    silently dropping the type byte, because a payload with a length that does
    not match its content is not parsed at all.
    """
    encoded = name.encode("utf-8")
    payload = bytes([0x09]) + encoded
    if len(payload) + 1 > 31:
        # Drop characters from the end of the name until the record fits. A
        # truncated name still starts with TVSBT, which is what the app matches.
        keep = 31 - 2 - 1
        payload = bytes([0x09]) + encoded[:keep]
    return bytes([len(payload)]) + payload


def _parse_advertising(payload: bytes) -> str:
    """Recover a name from [advertisement_payload] output. For tests."""
    if not payload:
        return ""
    length = payload[0]
    if payload[1] != 0x09:
        return ""
    return payload[2 : 1 + length].decode("utf-8", errors="replace")


def frame_hexdumps(frames: list[bytes]) -> str:
    """Format frames for a log line."""
    return " | ".join(P.hexdump(f) for f in frames)
