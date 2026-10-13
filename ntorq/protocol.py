# SmartXonnect BLE wire format, as the cluster speaks it.
#
# This is the mirror image of `app/src/main/java/dev/tvsopen/protocol/` in
# TVSRideCompanion. Where that file builds frames the *phone* sends, this one
# builds the frames the *cluster* sends and parses the ones the phone sends.
# The two must agree byte for byte; that agreement is the whole point of the
# simulator, and `tests/test_protocol.py` pins it against the captures quoted
# in TVSRideCompanion/docs/PROTOCOL.md.
#
# Every inbound multi-byte field is BIG-endian. That is not a guess: the
# vendor's own `DataParser.extractValue` appends bytes in order and parses the
# result as one hex number, and the owner's cluster was photographed reading
# 16593 km while the frame carried `02 88 2A`. Read the other way round it
# says 278733 km, on a bike ridden 16593 km.

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import IntEnum

# --------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------

#: Every SmartXonnect frame this simulator emits is exactly 20 bytes.
#: Confirmed on hardware across 501 frames in the 2026-10-13 session.
FRAME_SIZE = 20

#: End-of-frame marker at index 19 of every fixed-length frame.
END_BYTE = 0xFF


class StartId(IntEnum):
    """Frame lead bytes. `FrameConstants.StartId` in the official DEX."""

    A5 = 0x5A  # control frames, and every inbound speedometer frame
    B5 = 0x5B  # outbound text frames; also the 5B 42 identity frame
    B9 = 0x9B  # inbound handshake challenge
    A9 = 0x9A  # outbound handshake response


class ClusterDataId(IntEnum):
    """Byte 1 of a frame the *cluster* sends. Dispatch is on this byte."""

    SPEEDOMETER_1 = 0x10  # speed, odometer, fuel, trip, rpm  - the fast one
    SPEEDOMETER_2 = 0x11  # vehicle state, tell-tales, sw version
    SPEEDOMETER_3 = 0x19  # economy, distance to empty, OTA   (never seen live)
    SPEEDOMETER_4 = 0x18  # engine diagnostics, battery voltage
    SPEEDOMETER_16 = 0x16  # unknown layout; see UNKNOWN_16 below
    IDENTITY = 0x42  # 5B 42 - the cluster's own BLE address
    CALIBRATION = 0x37  # calibration response
    MUSIC = 0x6B  # cluster button -> media command
    MUSIC_ACK = 0x54  # the ACK half of the pair above
    WIFI_PASSWORD = 0x29


class PhoneDataId(IntEnum):
    """Byte 1 of a frame the *phone* sends. A different namespace."""

    MOBILE_DATA = 0x4A  # keepalive / find-my-scooter
    CALLER = 0x43  # caller ID
    NAVIGATION_CONTROL = 0x4E  # arrow + distances
    NAVIGATION_TEXT = 0x4F  # nav text row 1
    NAVIGATION_TEXT_2 = 0x50  # nav text row 2
    TEXT_ROW_1 = 0x4C  # message / caller / song title
    TEXT_ROW_2 = 0x63  # message row 2
    CHALLENGE_RESPONSE = 0xF1


UNKNOWN_16_NOTE = """\
`5A 16` arrived 136 times in a 501-frame capture - the second most common frame
on the cluster - and it is not in `NtorqFrameIdentifier.getFrameConfig()` at all.
The official app has no table for it, so TVSRideCompanion decodes nothing from
it. Its layout is unknown.

That makes it the most useful thing this simulator can invent. `Frame16Spec`
lets the raw bytes be set per field with a name and a width, so a hypothesis
can be checked against a live-looking stream without editing the phone. The
field names below are *placeholders* - they are not claims about what the bytes
mean, only labels so a capture can be annotated.
"""


# --------------------------------------------------------------------------
# Byte helpers
# --------------------------------------------------------------------------


def put_u8(buf: bytearray, offset: int, value: int) -> None:
    buf[offset] = value & 0xFF


def put_u16(buf: bytearray, offset: int, value: int) -> None:
    """Big-endian 16-bit. See the module docstring for why it is this way."""
    buf[offset] = (value >> 8) & 0xFF
    buf[offset + 1] = value & 0xFF


def put_u24(buf: bytearray, offset: int, value: int) -> None:
    buf[offset] = (value >> 16) & 0xFF
    buf[offset + 1] = (value >> 8) & 0xFF
    buf[offset + 2] = value & 0xFF


def get_u8(buf: bytes, offset: int) -> int:
    return buf[offset] & 0xFF


def get_u16(buf: bytes, offset: int, endian: str = "big") -> int:
    pair = (buf[offset] << 8) | (buf[offset + 1])
    return pair if endian == "big" else ((buf[offset + 1] << 8) | buf[offset])


def get_u24(buf: bytes, offset: int, endian: str = "big") -> int:
    if endian == "big":
        return (buf[offset] << 16) | (buf[offset + 1] << 8) | buf[offset + 2]
    return (buf[offset + 2] << 16) | (buf[offset + 1] << 8) | buf[offset]


def checksum(buf: bytearray, up_to: int | None = None) -> int:
    """`255 - (sum(bytes[0:up_to]) % 256)`, from `BluetoothUtil.addChecksum`.

    Only the frames that declare one carry it. The official mobile-data
    builder never writes a checksum, and neither do the navigation frames -
    that was verified by decompiling all three `returnMobilePacket` variants.
    The enriched third-party layout *does* write one at index 18, so this
    exists for that and for the calibration frame.
    """
    if up_to is None:
        up_to = len(buf) - 2
    return (255 - (sum(buf[:up_to]) % 256)) & 0xFF


def new_frame(start: int, data_id: int) -> bytearray:
    """A 20-byte frame with the lead bytes set and a terminator."""
    buf = bytearray(FRAME_SIZE)
    buf[0] = start & 0xFF
    buf[1] = data_id & 0xFF
    buf[FRAME_SIZE - 1] = END_BYTE
    return buf


def hexdump(raw: bytes) -> str:
    """Space-separated upper-case hex, the form both logs use."""
    return " ".join(f"{b:02X}" for b in raw)


def format_mac(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw)


# --------------------------------------------------------------------------
# Cluster -> phone
# --------------------------------------------------------------------------


@dataclass
class Speedometer1:
    """`NtorqSpeedOMeter1` - data id 0x10. The frame that carries the dash."""

    speed_kmh: int = 0  # [2]
    odometer_tenths: int = 0  # [3..5] big-endian; app divides by 10
    fuel_raw: int = 0  # [6] low nibble is the bar count; see ClusterState
    average_speed_kmh: int = 0  # [7]
    trip_status: int = 0  # [8] SELECTED_TRIP: 0=ODO 1=A 2=B 3=F
    top_speed_kmh: int = 0  # [9]
    throttle_percent: int = 0  # [10] app divides by 2
    backlight: int = 0  # [11] high nibble
    bt_pair_mode: int = 0  # [11] low nibble
    location_tag: int = 0  # [12]
    time_to_60_tenths: int = 0  # [13] app divides by 10
    trip_f_tenths: int = 0  # [14..16]
    engine_rpm: int = 0  # [17..18]


@dataclass
class Speedometer2:
    """`NtorqSpeedOMeter2` - data id 0x11. Tell-tales and identity."""

    fuel_sensor_fault: int = 0  # [2] a fault flag, NOT a level
    vehicle_state: int = 0  # [3]
    service_reminder: int = 0  # [4] the spanner icon
    vehicle_state_1: int = 0  # [6]
    software_version: int = 0  # [7] seen as 0x12 on the owner's cluster
    mil_blink_code: int = 0  # [8]
    vehicle_model: int = 0  # [9]
    vehicle_diagnostics: int = 0  # [10]
    isg_blink_code: int = 0  # [11]
    turn_indicator_status: int = 0  # [12] 0 off, 1 left, 2 right, 3 both
    connector_status: int = 0  # [13]


@dataclass
class Speedometer3:
    """`NtorqSpeedOMeter3` - data id 0x19.

    In the APK this frame carries economy and distance-to-empty. The owner's
    cluster **never sent it** in a 501-frame capture. It is emitted here only
    when the operator asks for it, which is itself a useful test: the phone
    decodes this frame, and its tiles must not light up from a frame the real
    cluster does not produce.
    """

    iss_duration: int = 0  # [2..4]
    ota_status: int = 0  # [5]
    average_fuel_economy: int = 0  # [8] km/L
    instantaneous_fuel_economy: int = 0  # [9]
    distance_to_empty_km: int = 0  # [11..12]
    iss_count: int = 0  # [15..17]


@dataclass
class Speedometer4:
    """`NtorqSpeedOMeter4` - data id 0x18. Engine diagnostics block."""

    engine_load: int = 0  # [2]
    accumulated_fuel_injection_time: int = 0  # [3..4]
    barometric_pressure: int = 0  # [6]
    intake_air_temperature: int = 0  # [7]
    engine_temperature_c: int = 0  # [8]
    fuel_injection_time: int = 0  # [9..10]
    battery_voltage: int = 0  # [11] seen as 0x64 = 100
    engine_running_time: int = 0  # [12..13]
    distance_traveled: int = 0  # [14..15]
    fuel_injection_volume: int = 0  # [16..17]


@dataclass
class Frame16Spec:
    """One named field of the unmapped `5A 16` frame."""

    name: str
    offset: int
    width: int = 1


#: Placeholder field map for 0x16. Names are labels for annotating captures,
#: not claims - see [UNKNOWN_16_NOTE].
FRAME_16_FIELDS: list[Frame16Spec] = [
    Frame16Spec("b2", 2),
    Frame16Spec("b3", 3),
    Frame16Spec("b4", 4),
    Frame16Spec("b5", 5),
    Frame16Spec("b6", 6),
    Frame16Spec("b7", 7, 2),
    Frame16Spec("b9", 9, 2),
    Frame16Spec("b11", 11),
    Frame16Spec("b12", 12),
    Frame16Spec("b13", 13),
    Frame16Spec("b14", 14, 3),
]

#: The bytes the owner's cluster sent for `5A 16`, verbatim from
#: TVSRideCompanion/docs/PROTOCOL.md section 4a:
#:
#: ```
#: 5A 16 00 00 00 00 00 03 28 00 45 00 00 00 96 09 ...
#: ```
#:
#: Sixteen bytes are quoted; a frame is twenty, so the tail is not known. The
#: default stream uses these sixteen and zero-fills the rest, and a frame that
#: pads with zeros is indistinguishable from one that does not, so nothing is
#: being claimed about bytes 15-18.
FRAME_16_CAPTURED = bytes.fromhex("5A160000000000032800450000009609")

#: How many bytes of the frame that quote actually covers.
FRAME_16_CAPTURED_LENGTH = 16


def encode_speedometer1(s: Speedometer1) -> bytes:
    """Build `5A 10`. Verified against the captured frame:

    `5A 10 00 02 88 2A 02 00 00 00 09 50 00 ...`
      ^^^^^^^ 0x02882A = 165930 tenths = 16593.0 km, which is the dash.
    """
    buf = new_frame(StartId.A5, ClusterDataId.SPEEDOMETER_1)
    put_u8(buf, 2, s.speed_kmh)
    put_u24(buf, 3, s.odometer_tenths & 0xFFFFFF)
    put_u8(buf, 6, s.fuel_raw)
    put_u8(buf, 7, s.average_speed_kmh)
    put_u8(buf, 8, s.trip_status)
    put_u8(buf, 9, s.top_speed_kmh)
    put_u8(buf, 10, s.throttle_percent)
    put_u8(buf, 11, ((s.backlight & 0x0F) << 4) | (s.bt_pair_mode & 0x0F))
    put_u8(buf, 12, s.location_tag)
    put_u8(buf, 13, s.time_to_60_tenths)
    put_u24(buf, 14, s.trip_f_tenths & 0xFFFFFF)
    put_u16(buf, 17, s.engine_rpm)
    return bytes(buf)


def encode_speedometer2(s: Speedometer2) -> bytes:
    buf = new_frame(StartId.A5, ClusterDataId.SPEEDOMETER_2)
    put_u8(buf, 2, s.fuel_sensor_fault)
    put_u8(buf, 3, s.vehicle_state)
    put_u8(buf, 4, s.service_reminder)
    put_u8(buf, 6, s.vehicle_state_1)
    put_u8(buf, 7, s.software_version)
    put_u8(buf, 8, s.mil_blink_code)
    put_u8(buf, 9, s.vehicle_model)
    put_u8(buf, 10, s.vehicle_diagnostics)
    put_u8(buf, 11, s.isg_blink_code)
    put_u8(buf, 12, s.turn_indicator_status)
    put_u8(buf, 13, s.connector_status)
    return bytes(buf)


def encode_speedometer3(s: Speedometer3) -> bytes:
    buf = new_frame(StartId.A5, ClusterDataId.SPEEDOMETER_3)
    put_u24(buf, 2, s.iss_duration & 0xFFFFFF)
    put_u8(buf, 5, s.ota_status)
    put_u8(buf, 8, s.average_fuel_economy)
    put_u8(buf, 9, s.instantaneous_fuel_economy)
    put_u16(buf, 11, s.distance_to_empty_km)
    put_u24(buf, 15, s.iss_count & 0xFFFFFF)
    return bytes(buf)


def encode_speedometer4(s: Speedometer4) -> bytes:
    buf = new_frame(StartId.A5, ClusterDataId.SPEEDOMETER_4)
    put_u8(buf, 2, s.engine_load)
    put_u16(buf, 3, s.accumulated_fuel_injection_time)
    put_u8(buf, 6, s.barometric_pressure)
    put_u8(buf, 7, s.intake_air_temperature)
    put_u8(buf, 8, s.engine_temperature_c)
    put_u16(buf, 9, s.fuel_injection_time)
    put_u8(buf, 11, s.battery_voltage)
    put_u16(buf, 12, s.engine_running_time)
    put_u16(buf, 14, s.distance_traveled)
    put_u16(buf, 16, s.fuel_injection_volume)
    return bytes(buf)


def encode_frame_16(fields: dict[str, int] | None = None) -> bytes:
    """Build `5A 16` from named fields, or from the captured bytes verbatim.

    With no arguments this reproduces the exact frame the owner's cluster
    emitted, so an idle simulator looks like a stationary cluster with the
    ignition on. Pass a dict to test a hypothesis about the layout.
    """
    buf = new_frame(StartId.A5, ClusterDataId.SPEEDOMETER_16)
    if fields is None:
        buf[2 : FRAME_16_CAPTURED_LENGTH] = FRAME_16_CAPTURED[2:FRAME_16_CAPTURED_LENGTH]
        return bytes(buf)
    for spec in FRAME_16_FIELDS:
        if spec.name in fields:
            value = int(fields[spec.name]) & ((1 << (8 * spec.width)) - 1)
            for i in range(spec.width):
                buf[spec.offset + i] = (value >> (8 * (spec.width - 1 - i))) & 0xFF
    return bytes(buf)


def encode_identity(address: str) -> bytes:
    """Build `5B 42` - the cluster's own BLE address, bytes 7..12.

    Arrived six times in the 501-frame capture. `5B 42 00 00 00 00 00 <MAC> ...`

    The default address is deliberately a locally administered, obviously
    synthetic address. The real cluster's MAC is the rider's, it is in the
    app's screenshots, and it must never reach a public repository.
    """
    parts = [int(p, 16) for p in address.split(":")]
    if len(parts) != 6:
        raise ValueError(f"not a MAC address: {address!r}")
    buf = new_frame(StartId.B5, ClusterDataId.IDENTITY)
    buf[7:13] = bytes(parts)
    return bytes(buf)


def encode_challenge(challenge: bytes = b"\x00" * 16) -> bytes:
    """Build `9B F2` - the handshake challenge.

    **The owner's Ntorq 125 never sent one.** No pairing mode was needed and
    no challenge arrived in the whole session, so the simulator does not send
    one unless the operator asks. It exists because some units do use it and
    the phone's `Handshake.kt` answers it; turning it on tests that path
    without hardware.
    """
    buf = new_frame(StartId.B9, 0xF2)
    buf[2:18] = challenge.ljust(16, b"\x00")[:16]
    return bytes(buf)


# --------------------------------------------------------------------------
# Phone -> cluster
# --------------------------------------------------------------------------


@dataclass
class MobileDataPacket:
    """`5B 4A` - the keepalive, as the official app builds it.

    There is no checksum byte. `returnMobilePacket`,
    `returnValidMobilePacket` and `returnMobilePacketWithACK` all write zeros
    into index 18 and none of them calls the checksum routine.
    """

    signal_bars: int = 0
    battery_bars: int = 0
    incoming_call: bool = False
    call_muted: bool = False
    network_type: int = 0x04
    ack: bool = False
    time_hms: tuple[int, int, int] | None = None
    temperature_c: int | None = None
    find_my_scooter: bool = False


@dataclass
class NavControl:
    """`5A 4E` - arrow, distance, ETA, row count, active flag."""

    distance_m: int = 0
    eta_minutes: int = 0
    remaining_m: int = 0
    pictogram: int = 0
    rows: int = 1
    active: bool = True


@dataclass
class TextRow:
    """`5B 4F` / `5B 0x50` / `5B 0x4C` / `5B 0x63` - a plain text line."""

    text: str = ""
    data_id: int = PhoneDataId.NAVIGATION_TEXT


@dataclass
class HandshakeResponse:
    """`9A F1` - the answer to a challenge."""

    ciphertext: bytes = b""


@dataclass
class UnknownPhoneFrame:
    """Anything the phone sent that this simulator does not model."""

    data_id: int
    raw: bytes


@dataclass
class PhoneFrame:
    """A decoded phone -> cluster frame, with the ambiguity kept visible."""

    kind: str
    raw: bytes
    data_id: int
    payload: object = None
    #: Free-text warnings the operator should see rather than swallow.
    notes: list[str] = field(default_factory=list)


#: `PacketBuilder.putUInt16` in TVSRideCompanion writes **little-endian**, while
#: every field this simulator reads back from the cluster is big-endian and is
#: confirmed so twice over. Both readings are therefore plausible for the
#: outbound navigation frame, and nothing on the wire has settled it.
ENDIAN_AMBIGUOUS_NOTE = (
    "navigation control frame decoded as both big- and little-endian; "
    "the outbound byte order is not established on hardware"
)


def decode_phone_frame(raw: bytes) -> PhoneFrame | None:
    """Parse one frame written by the phone. Never raises on bad input."""
    if len(raw) < 3:
        return None
    start = raw[0]
    data_id = raw[1]

    if start == StartId.A5 and data_id in (
        PhoneDataId.NAVIGATION_CONTROL,
        0x49,  # NAVIGATION_CONTROL_2, listed upstream, never used
    ):
        return _decode_nav_control(raw, data_id)

    if start == StartId.B5:
        if data_id == PhoneDataId.MOBILE_DATA:
            return _decode_mobile_data(raw)
        if data_id in (
            PhoneDataId.NAVIGATION_TEXT,
            PhoneDataId.NAVIGATION_TEXT_2,
            PhoneDataId.TEXT_ROW_1,
            PhoneDataId.TEXT_ROW_2,
            PhoneDataId.CALLER,
            0x53,  # SMS
        ):
            return decode_text(raw, data_id)
        return PhoneFrame("unknown", raw, data_id, UnknownPhoneFrame(data_id, raw))

    if start in (StartId.A9, StartId.B9) and data_id == PhoneDataId.CHALLENGE_RESPONSE:
        return PhoneFrame(
            "handshake-response",
            raw,
            data_id,
            HandshakeResponse(raw[2:18]),
        )

    if start == StartId.B9 and data_id == 0xF2:
        return PhoneFrame("challenge", raw, data_id, bytes(raw[2:18]))

    if start == StartId.A5 and data_id == 0x37:
        return PhoneFrame("calibration", raw, data_id, dict(raw))

    return PhoneFrame("unknown", raw, data_id, UnknownPhoneFrame(data_id, raw))


def _decode_nav_control(raw: bytes, data_id: int) -> PhoneFrame:
    """Decode `5A 4E`, keeping both endianness readings.

    A distance of 550 m written little-endian is the bytes ``26 02``; read
    big-endian the same two bytes are 9730. On a frame the app built, only the
    writer knows which was meant, and the cluster's tolerance for either has
    never been observed. Both are reported.
    """
    distance_be = get_u16(raw, 2, "big")
    distance_le = get_u16(raw, 2, "little")
    eta_be = get_u16(raw, 4, "big")
    eta_le = get_u16(raw, 4, "little")
    remaining_be = get_u24(raw, 6, "big")
    remaining_le = get_u24(raw, 6, "little")

    payload = NavControl(
        distance_m=distance_be,
        eta_minutes=eta_be,
        remaining_m=remaining_be,
        pictogram=get_u8(raw, 9),
        rows=get_u8(raw, 10),
        active=get_u8(raw, 11) != 0,
    )

    notes = [ENDIAN_AMBIGUOUS_NOTE]
    if distance_be != distance_le:
        notes.append(
            f"distance is {distance_be} m big-endian or {distance_le} m "
            f"little-endian; remaining is {remaining_be} m or {remaining_le} m"
        )
    if eta_be != eta_le:
        notes.append(f"ETA is {eta_be} or {eta_le} minutes depending on byte order")
    if get_u8(raw, 19) != END_BYTE:
        notes.append(
            f"byte 19 is 0x{get_u8(raw, 19):02X}, not the 0xFF terminator"
        )
    if any(get_u8(raw, i) for i in range(12, 19)):
        notes.append("padding bytes 12..18 are not zero")
    if get_u8(raw, 10) not in (1, 2):
        notes.append(
            f"row count is {get_u8(raw, 10)}; the reference implementation "
            "hardcodes 1 and the cluster's text area has three rows"
        )

    alt = NavControl(
        distance_m=distance_le,
        eta_minutes=eta_le,
        remaining_m=remaining_le,
        pictogram=get_u8(raw, 9),
        rows=get_u8(raw, 10),
        active=get_u8(raw, 11) != 0,
    )
    return PhoneFrame(
        "nav-control",
        raw,
        data_id,
        _NavControlWithAlt(payload, alt),
        notes,
    )


@dataclass
class _NavControlWithAlt:
    """A [NavControl] plus the other-endian reading of the same bytes."""

    big: NavControl
    little: NavControl

    def __getattr__(self, item):  # delegate so callers can read .distance_m
        return getattr(self.big, item)


def _decode_mobile_data(raw: bytes) -> PhoneFrame:
    notes: list[str] = []
    time_hms = None
    temperature_c = None

    if get_u8(raw, 6) and get_u8(raw, 8) and get_u8(raw, 9):
        # Enriched layout: hour/minute/second at 6..8. The official builder
        # leaves 6..10 zero, so a non-zero triple means the third-party
        # Jupiter shape, which does carry a checksum at [18].
        time_hms = (get_u8(raw, 6), get_u8(raw, 7), get_u8(raw, 8))
        if raw[4]:
            temperature_c = (raw[4] + 256 if raw[4] > 127 else raw[4]) - 40
    if get_u8(raw, 2) > 5 or get_u8(raw, 3) > 5:
        notes.append(
            f"byte 2 is {get_u8(raw, 2)} and byte 3 is {get_u8(raw, 3)}; "
            "the official layout allows 0..5 for each"
        )
    if get_u8(raw, 18):
        notes.append(
            f"byte 18 is 0x{get_u8(raw, 18):02X} - the official mobile-data "
            "packet carries no checksum there, so this is the enriched layout's"
        )
        expected = checksum(raw, 18)
        if get_u8(raw, 18) != expected:
            notes.append(
                f"and it does not match: expected 0x{expected:02X}"
            )
    if get_u8(raw, 17):
        notes.append("byte 17 set - the enriched layout's find-my-scooter flag")

    payload = MobileDataPacket(
        signal_bars=get_u8(raw, 2),
        battery_bars=get_u8(raw, 3),
        incoming_call=get_u8(raw, 4) != 0,
        call_muted=get_u8(raw, 5) != 0,
        network_type=get_u8(raw, 11),
        ack=get_u8(raw, 12) == 0x01,
        time_hms=time_hms,
        temperature_c=temperature_c,
        find_my_scooter=get_u8(raw, 17) != 0,
    )
    return PhoneFrame("mobile-data", raw, PhoneDataId.MOBILE_DATA, payload, notes)


#: Characters the cluster can actually draw.
#:
#: `BluetoothUtil.createMobileToClusterArray` and `isEnglishCharacters` in the
#: official APK: emoji and symbol codepoints collapse to `@`, and anything
#: outside `[A-Za-z0-9 ]` is not renderable at all. TVSRideCompanion's
#: `PacketBuilder.sanitize` implements that with two deliberate departures
#: (`+` and `-` kept, other punctuation folded to a single space), and those
#: departures are reproduced here so the simulator renders what the phone
#: actually intended to send - and flags the parts it cannot be sure of.
KEPT_PUNCTUATION = "+-"
TEXT_ROW_CHARS = 17


def decode_text(raw: bytes, data_id: int) -> PhoneFrame:
    """Decode one of the plain-text rows.

    The payload is bytes 2..18, zero-padded. It is decoded as latin-1 rather
    than UTF-8 on purpose: the phone sanitises to ASCII, but when it does not,
    a UTF-8 sequence read strictly would either raise or vanish, and the point
    of this function is to show the operator exactly what arrived.
    """
    payload = raw[2:19]
    text = payload.rstrip(b"\x00").decode("latin-1")
    notes: list[str] = []

    non_ascii = [c for c in text if not (0x20 <= ord(c) <= 0x7E)]
    if non_ascii:
        notes.append(
            "carries bytes outside printable ASCII ("
            + ", ".join(f"0x{ord(c):02X}" for c in non_ascii[:6])
            + "); the cluster cannot render these"
        )
    if any(c in text for c in KEPT_PUNCTUATION):
        notes.append(
            "'+' or '-' survived the sanitiser - both are ASCII printable, "
            "but whether this cluster renders them is unverified"
        )
    if len(text) >= TEXT_ROW_CHARS:
        notes.append(
            f"{len(text)} characters: this fills the packet's text field, so "
            "nothing was truncated here. The cluster's display is narrower "
            "still - two rows of ten cells, counted off the segment grid in "
            "the owner's manual - so more than ten characters per packet has to "
            "be wrapped, scrolled or dropped by the cluster."
        )
    if get_u8(raw, 19) != END_BYTE:
        notes.append(f"byte 19 is 0x{get_u8(raw, 19):02X}, not the 0xFF terminator")

    return PhoneFrame("text", raw, data_id, TextRow(text, data_id), notes)


def now_hms() -> tuple[int, int, int]:
    now = _dt.datetime.now()
    return now.hour, now.minute, now.second
