# Advertising the cluster's name, so the phone's scan can find it.
#
# Split from `server.py` because advertising and serving are different problems
# and different failure modes. BlueZ registers a GATT service by object path
# and then advertises it, or advertises without one; the app's scanner found the
# real cluster by **name alone**, and reproducing that omission is the point.
#
# The rule this module exists to enforce: **advertise nothing but the name.**
# The owner's cluster advertised no service UUID, which is precisely why
# TVSRideCompanion needed a name-based fallback in its scan. A simulator that
# advertised the service UUID would let a scan filtered on the documented UUID
# succeed, and that scan fails against the real vehicle - so advertising the
# service would hide a bug in the exact code path the project needs tested.

from __future__ import annotations

import asyncio
from dataclasses import dataclass

#: Bluetooth Core Specification, Vol 3, Part C, 11.2: advertising and
#: scan response data types.
ADV_TYPE_FLAGS = 0x01
ADV_TYPE_COMPLETE_LOCAL_NAME = 0x09
ADV_TYPE_SHORTENED_LOCAL_NAME = 0x08
ADV_TYPE_SERVICE_UUIDS_16BIT = 0x03
ADV_TYPE_SERVICE_UUIDS_128BIT = 0x07
ADV_TYPE_APPEARANCE = 0x19

#: GAP appearance for a generic device.
APPEARANCE_GENERIC = 0x0000

#: Flags: LE General Discoverable, BR/EDR not supported.
FLAGS_LE_GENERAL_DISCOVERABLE = 0x06

#: The legacy advertising payload is 31 bytes. Anything longer is not sent.
MAX_ADVERTISING_DATA = 31

#: The prefix TVSRideCompanion's scanner matches on. The manual states the
#: cluster's Bluetooth name "is prefixed by TVSBT and followed by
#: alphanumeric digits. Ex: 'TVSBT12345'", and the rider's own cluster was seen
#: advertising as TVSBT followed by nine digits.
NAME_PREFIX = "TVSBT"

#: The SmartXonnect service UUID without its dashes, kept here rather than
#: imported so this module has no dependency on the rest of the package and can
#: be read on its own.
SERVICE_UUID_HEX = "5456534D564753415342454E544F5251"

#: The scan filter in the app is `name.startsWith("TVSBT")`, so the name is the
#: whole of the discovery mechanism. It has to be the complete local name, not
#: the shortened form: the shortened variant is truncated by the advertising
#: length and would not carry all ten characters.


@dataclass
class AdvertisingData:
    """A built advertising payload, with the reasoning kept alongside it."""

    #: Legacy advertising data bytes.
    payload: bytes
    #: Scan response data, when the name did not fit alongside the rest.
    scan_response: bytes | None
    #: Human-readable summary for the log.
    description: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.description


def build_advertising(
    name: str,
    *,
    include_service_uuid: bool = False,
    include_flags: bool = False,
) -> AdvertisingData:
    """Build the advertising payload for a cluster.

    [include_service_uuid] defaults to False and should stay that way. It exists
    so the difference can be demonstrated rather than described: building it
    with True makes a UUID-filtered scan succeed, which is exactly the
    behaviour that is wrong.

    ## Why the name goes in the scan response

    A 128-bit service UUID record is 18 bytes and a twelve-character name is 14,
    so they cannot both fit in the 31-byte advertising payload - one record has
    to go somewhere else. The name goes to the **scan response**, which is what
    a real device does and what the app's scanner reads: a scan with no filter
    still sees the device and its name.

    Putting the UUID in the scan response instead would leave the name in the
    advertising data, which also works, but the name is the field the app
    filters on, and a device whose *advertising* payload is only a service UUID
    is invisible to a prefix scan in more stacks than one that at least
    advertises a name. Keeping the name in the primary payload is the more
    robust of the two, and the primary payload is also what a scan that does not
    wait for a scan response sees.
    """
    name_bytes = name.encode("utf-8")

    def name_record() -> bytearray:
        record = bytearray()
        record.append(0x00)  # length placeholder
        record.append(ADV_TYPE_COMPLETE_LOCAL_NAME)
        record.extend(name_bytes)
        return record

    notes: list[str] = []
    scan_response: bytes | None = None

    if include_service_uuid:
        payload = bytearray()
        payload.append(0x00)  # length placeholder
        if include_flags:
            payload.append(ADV_TYPE_FLAGS)
            payload.append(FLAGS_LE_GENERAL_DISCOVERABLE)
        # The service UUID, as a raw 128-bit value with the Bluetooth base
        # suffix stripped. This is the whole of what the official app declares,
        # and adding it is what makes a UUID-filtered scan succeed where it
        # fails against the real cluster.
        payload.append(ADV_TYPE_SERVICE_UUIDS_128BIT)
        payload.extend(bytes.fromhex(SERVICE_UUID_HEX))
        _truncate_to_fit(payload, MAX_ADVERTISING_DATA, tail_is_name=False)

        # The name does not fit alongside it, so it goes in the scan response.
        response = name_record()
        truncated = _truncate_to_fit(response, MAX_ADVERTISING_DATA, tail_is_name=True)
        scan_response = bytes(response)

        notes.append("advertising the service UUID - NOT what the real cluster does")
        notes.append("name moved to the scan response to fit both")
        if truncated:
            notes.append("the name was trimmed; the app's prefix filter still matches")
    else:
        payload = bytearray()
        payload.append(0x00)
        if include_flags:
            payload.append(ADV_TYPE_FLAGS)
            payload.append(FLAGS_LE_GENERAL_DISCOVERABLE)
        payload.append(ADV_TYPE_COMPLETE_LOCAL_NAME)
        payload.extend(name_bytes)
        _truncate_to_fit(payload, MAX_ADVERTISING_DATA, tail_is_name=True)
        notes.append("name only, as measured on the real cluster")

    description = (
        f"advertising as {name!r} ({len(name_bytes)} bytes of name, "
        f"{len(payload)} byte advertising payload"
        + (f", {len(scan_response)} byte scan response" if scan_response else "")
        + f", {', '.join(notes)})"
    )
    return AdvertisingData(bytes(payload), scan_response, description)


def _truncate_to_fit(payload: bytearray, limit: int, tail_is_name: bool) -> bool:
    """Shorten [payload] to [limit] bytes, patching the leading length byte.

    Returns whether anything was cut. Truncation always takes from the **end**,
    because the app's filter is a prefix test: a shorter name still starts with
    `TVSBT` and is still found, while dropping the head would make the device
    invisible. Nothing is dropped from the front of a record, so every remaining
    record stays well-formed - a record whose length overruns the payload is not
    parsed at all, which looks exactly like a device that is not advertising.
    """
    if len(payload) <= limit:
        payload[0] = len(payload) - 1
        return False

    excess = len(payload) - limit
    del payload[len(payload) - excess :]
    payload[0] = len(payload) - 1
    return True


def parse_name(*payloads: bytes | None) -> str | None:
    """Recover a device name from advertising data.

    Walks the length-prefixed records the way a scanner does. Pass the
    advertising payload first and the scan response second; the first one that
    carries a name wins, which is the order a scan collects them in.

    Returns the complete local name, or the shortened one if that is all there
    is, or None if neither is present.
    """
    for payload in payloads:
        if not payload:
            continue
        index = 0
        while index + 1 < len(payload):
            length = payload[index]
            if length == 0:
                break
            record_type = payload[index + 1]
            body = payload[index + 2 : index + 1 + length]
            index += 1 + length

            if record_type == ADV_TYPE_COMPLETE_LOCAL_NAME:
                return body.decode("utf-8", errors="replace")
            if record_type == ADV_TYPE_SHORTENED_LOCAL_NAME:
                return body.decode("utf-8", errors="replace")
    return None


def device_name(data: AdvertisingData) -> str | None:
    """The name a scanner would report for [data]."""
    return parse_name(data.payload, data.scan_response)


def matches_app_filter(name: str | None, prefix: str = NAME_PREFIX) -> bool:
    """Whether TVSRideCompanion's scan filter would pick this name up.

    Mirrors the filter the app applies, so a test can assert that the
    advertising data this module builds is findable by the code under test.
    """
    return bool(name) and name.startswith(prefix)


async def advertise(
    name: str,
    *,
    include_service_uuid: bool = False,
    duration: float | None = None,
) -> None:
    """Advertise [name] over BLE using BlueZ, for [duration] seconds.

    Uses `bleak`'s low-level BlueZ backend through `bleak[backend]`, which
    drives `org.bluez.LEAdvertisingManager1` directly. `bleak` is not a
    dependency of this project; the import is local to this function so the
    rest of the simulator works without it, and the failure mode is a clear
    message rather than an ImportError at startup.
    """
    try:
        from bleak.backends.bluezdbus.manager import (
            BlueZDBusManager,
            BlueZLEAdvertisement,
        )
    except ImportError:
        raise RuntimeError(
            "advertising needs the 'bleak' package (pip install bleak). It is "
            "deliberately not a dependency of this project: the GATT server "
            "needs nothing beyond the standard library and dbus-python, and "
            "only this function pulls anything extra."
        ) from None

    data = build_advertising(name, include_service_uuid=include_service_uuid)

    # Service UUIDs go to `bleak` as structured values, not as hand-built
    # records: it assembles the advertising record itself and will put it in the
    # right place. Handing it the name as well puts the name in the primary
    # payload and the UUID alongside, which is what `build_advertising` already
    # worked out does not fit in 31 bytes.
    service_uuids = []
    if include_service_uuid:
        service_uuids.append(SERVICE_UUID_HEX.lower())

    manager = BlueZDBusManager()
    advertisement = BlueZLEAdvertisement(
        local_name=name if data.scan_response is None else None,
        service_uuids=service_uuids,
        service_data={},
        manufacturer_data={},
        advertising_type="peripheral",
        # When the name did not fit, it goes here instead.
        **({"scan_response_data": data.scan_response}
           if data.scan_response is not None else {}),
    )
    try:
        await manager.start_advertising(advertisement)
    except Exception as exc:  # noqa: BLE001 - BlueZ raises several types
        raise RuntimeError(
            f"BlueZ refused to start advertising: {exc}\n"
            "Check that the adapter supports peripheral mode. Many laptop "
            "adapters advertise only in a dual-role mode that needs Bluetooth "
            "turned off and on again; `bluetoothctl power off && bluetoothctl "
            "power on` is usually enough."
        ) from exc

    if duration is None:
        await asyncio.Event().wait()
    else:
        await asyncio.sleep(duration)


async def stop_advertising() -> None:
    """Stop whatever is advertising."""
    try:
        from bleak.backends.bluezdbus.manager import BlueZDBusManager
    except ImportError:
        return
    manager = BlueZDBusManager()
    await manager.stop_advertising()
