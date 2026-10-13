"""Byte-level conformance tests.

These are the tests that matter most in this repository, because the failure
they guard against is silent: a frame that is the wrong length, the wrong byte
order or one field too short still parses, still produces a number, and looks
plausible on a dashboard. The odometer reading ten times low in
TVSRideCompanion was exactly that - it compiled clean, produced no warning and
passed every on-device smoke test.

Each test that pins a byte says where the expected value came from.
"""

from __future__ import annotations

import pytest

from ntorq import protocol as P
from ntorq.cluster import (
    ClusterState,
    FUEL_HALF_L,
    FUEL_RESERVE_L,
    GAUGE_BARS,
    TEXT_ROW_CELLS,
    TEXT_ROWS,
)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


class TestFraming:
    def test_every_cluster_frame_is_twenty_bytes(self):
        """The capture's own rule: "every frame is exactly 20 bytes and ends
        0xFF", across 501 frames."""
        state = ClusterState()
        state.odometer_tenths = 165_930
        state.fuel_litres = FUEL_HALF_L
        state.emit_frame_19 = True
        frames = [
            P.encode_speedometer1(state.speedometer_1()),
            P.encode_speedometer2(state.speedometer_2()),
            P.encode_speedometer3(state.speedometer_3()),
            P.encode_speedometer4(state.speedometer_4()),
            P.encode_frame_16(),
            state.identity(),
        ]
        for raw in frames:
            assert len(raw) == P.FRAME_SIZE, P.hexdump(raw)
            assert raw[19] == 0xFF, P.hexdump(raw)

    def test_cluster_leads_with_start_byte_then_data_id(self):
        """`IncomingFrameIdentifier.identify()` indexes byte 1, and
        `BtData.isValidFrame()` accepts start bytes 0x9B and 0x5A."""
        state = ClusterState()
        assert P.encode_speedometer1(state.speedometer_1())[:2] == bytes([0x5A, 0x10])
        assert P.encode_speedometer2(state.speedometer_2())[:2] == bytes([0x5A, 0x11])
        assert P.encode_speedometer4(state.speedometer_4())[:2] == bytes([0x5A, 0x18])
        assert state.identity()[:2] == bytes([0x5B, 0x42])

    def test_checksum_is_penultimate_not_a_fixed_index(self):
        """`BluetoothUtil.addChecksum(byte[])` builds
        `[data[0], data[1], data[2..len-2], sum, 0xFF]` - the checksum lands at
        length-2, so a short frame does not put it at index 18."""
        for length in (8, 12, 20):
            buf = bytearray(range(1, length + 1))
            expected = (255 - (sum(buf[: length - 2]) % 256)) & 0xFF
            assert P.checksum(buf, length - 2) == expected


# ---------------------------------------------------------------------------
# Byte order - the bug this repository exists to prevent
# ---------------------------------------------------------------------------


class TestByteOrder:
    def test_odometer_is_big_endian_and_matches_the_dash(self):
        """The captured frame, quoted in docs/PROTOCOL.md section 4a:

            5A 10 00 02 88 2A 02 ...

        Bytes 3-5 are `02 88 2A` = 165930 tenths = **16593.0 km**, which is the
        figure on the cluster. Read little-endian the same three bytes are
        0x2A8802 = 2787330 tenths = 278733.0 km, on a bike ridden 16593 km.
        """
        state = ClusterState()
        state.odometer_tenths = 165_930
        raw = P.encode_speedometer1(state.speedometer_1())

        assert raw[3:6] == bytes([0x02, 0x88, 0x2A])

        decoded = P.get_u24(raw, 3, "big")
        assert decoded == 165_930
        assert decoded / 10 == 16_593.0

        wrong = P.get_u24(raw, 3, "little")
        assert wrong == 0x2A8802
        # The wrong reading is not a small error; it is a different bike.
        assert wrong / 10 > 16593 * 16

    def test_the_captured_frame_reproduces_byte_for_byte(self):
        """Building `5A 10` for a known cluster state must give back the exact
        bytes that were captured from it."""
        state = ClusterState()
        state.odometer_tenths = 165_930
        state.speed_kmh = 0
        state.fuel_litres = FUEL_RESERVE_L  # one bar, raw 1
        raw = P.encode_speedometer1(state.speedometer_1())
        # Bytes 0,1,3,4,5 and 19 are the parts the capture pins.
        assert raw[0] == 0x5A
        assert raw[1] == 0x10
        assert raw[2] == 0x00
        assert raw[3:6] == bytes([0x02, 0x88, 0x2A])
        assert raw[19] == 0xFF

    def test_every_multi_byte_field_is_big_endian(self):
        """Trip F, engine RPM and distance-travelled all went wrong together
        when the reader assembled little-endian, so all of them are pinned."""
        state = ClusterState()
        state.odometer_tenths = 165_930
        state.trip_f_tenths = 123_456
        state.engine_temp_c = 88
        raw = P.encode_speedometer1(state.speedometer_1())

        assert raw[14:17] == bytes([0x01, 0xE2, 0x40])  # 123456 tenths
        assert P.get_u24(raw, 14, "big") == 123_456

    def test_vendor_offset_table_maps_onto_our_indices(self):
        """`@ParsingMeta` is 1-based; every declared offset minus one gives our
        0-based index.

        The table, from docs/PROTOCOL.md:

        | vendor | length | ours  | field        |
        | 1      | 1      | 0     | START_BYTE   |
        | 3      | 1      | 2     | SPEED        |
        | 4      | 3      | 3-5   | ODOMETER     |
        | 7      | 1      | 6     | FUEL_LEVEL   |
        | 15     | 3      | 14-16 | TRIP_F_METER |
        | 18     | 2      | 17-18 | ENGINE_RPM   |
        | 20     | 1      | 19    | END_BYTE     |
        """
        state = ClusterState()
        state.speed_kmh = 47
        state.odometer_tenths = 165_930
        state.trip_f_tenths = 1_234
        state.engine_temp_c = 88
        speedometer1 = state.speedometer_1()
        sm1 = P.encode_speedometer1(speedometer1)

        assert sm1[0] == 0x5A  # START_BYTE
        assert sm1[2] == 47  # SPEED
        assert P.get_u24(sm1, 3, "big") == 165_930  # ODOMETER
        assert sm1[6] == state.fuel_bars()  # FUEL_LEVEL
        assert P.get_u24(sm1, 14, "big") == 1_234  # TRIP_F_METER
        assert P.get_u16(sm1, 17, "big") == speedometer1.engine_rpm
        assert sm1[19] == 0xFF  # END_BYTE

        # `5A 18` is a different layout: ENGINE_TEMPERATURE at index 8 and
        # BATTERY_VOLTAGE at index 11, per `NtorqSpeedOMeter4`.
        sm4 = P.encode_speedometer4(state.speedometer_4())
        assert sm4[8] == 88
        assert sm4[11] == 126  # 12.6 V, written as tenths of a volt


# ---------------------------------------------------------------------------
# Fuel
# ---------------------------------------------------------------------------


class TestFuel:
    def test_gauge_has_six_bars(self):
        """The manual states six bars for fuel and six for engine temperature,
        in so many words, twice."""
        assert GAUGE_BARS == 6

    def test_raw_fuel_7_is_reproducible_but_out_of_range(self):
        """The owner's cluster sent 7 in index 6 while the dash was
        photographed reading FUEL EMPTY. A six-bar gauge cannot express 7, and
        TVSRideCompanion now drops out-of-range values rather than clamping.

        This is the test that keeps the simulator honest about it: it can
        reproduce the frame real hardware sent, and the state model still
        refuses to render it as a level.
        """
        state = ClusterState()
        state.raw_fuel_override = 7
        raw = P.encode_speedometer1(state.speedometer_1())
        assert raw[6] == 7

        # The app takes the low nibble. 7 is what it sees.
        low_nibble = raw[6] & 0x0F
        assert low_nibble == 7
        assert low_nibble not in range(0, GAUGE_BARS + 1)

    def test_fuel_bar_mapping_follows_the_manual_litres(self):
        """The manual gives three points, not a formula: six bars above about
        5 L, three at 3.2 L, and the last bar blinking at 1.2 L. The mapping
        has to reproduce all three."""
        state = ClusterState()

        state.fuel_litres = 5.0
        assert state.fuel_bars() == 6

        state.fuel_litres = 3.2  # the manual's half-tank figure
        assert state.fuel_bars() == 3

        state.fuel_litres = FUEL_RESERVE_L
        assert state.fuel_bars() == 1
        assert state.fuel_at_reserve()

    def test_fuel_bars_are_monotonic_and_bounded(self):
        """A gauge that went up and down as the tank filled would be a
        simulator artefact, and would hide a decode bug rather than show one."""
        state = ClusterState()
        previous = -1
        for tenths in range(0, 51):
            state.fuel_litres = tenths / 10.0
            bars = state.fuel_bars()
            assert 0 <= bars <= GAUGE_BARS
            assert bars >= previous, f"{state.fuel_litres} L gave {bars} after {previous}"
            previous = bars

    def test_fuel_sensor_fault_is_not_a_level(self):
        """`0x11` index 2 is `fuelSensorFailure` upstream. Reading it as a level
        would produce the opposite error to the fuel gauge."""
        state = ClusterState()
        assert state.speedometer_2().fuel_sensor_fault == 0


# ---------------------------------------------------------------------------
# Text area geometry - the finding this simulator is for
# ---------------------------------------------------------------------------


class TestTextArea:
    def test_text_area_is_ten_cells_by_two_rows(self):
        """Counted off the segment grid in the manual's display-test
        illustration."""
        assert TEXT_ROW_CELLS == 10
        assert TEXT_ROWS == 2
        assert TEXT_ROW_CELLS * TEXT_ROWS == 20

    def test_a_seventeen_character_row_overflows(self):
        """TVSRideCompanion's `PacketBuilder` sends 17 characters per text row.
        The area holds ten, so seven characters per row have nowhere to go."""
        sent_by_app = 17
        assert sent_by_app > TEXT_ROW_CELLS
        overflow_per_row = sent_by_app - TEXT_ROW_CELLS
        assert overflow_per_row == 7

    def test_cluster_messages_measured_against_the_area(self):
        """Every message the manual lists, measured against twenty cells.

        One of them does not fit, and that is worth stating rather than
        rounding away. `CONNECTION SUCCESSFUL` is 21 characters. Either the
        cluster scrolls it, or it abbreviates it on screen, or the text area is
        in fact wider than the ten cells the segment grid shows. All three are
        consistent with what is documented, and nothing observed settles it.

        This is the same question the app runs into from the other side: it
        sends seventeen characters per row into a ten-cell area. The cluster
        has solved this problem for its own messages somehow, and how it does
        that is exactly what a capture would reveal.
        """
        messages = [
            "FUEL EMPTY",
            "ENGINE WARNING",
            "CHECK FUEL SENSOR",
            "3 MISSED CALLS",
            "SMS FROM AMIT",
            "CALL FROM AMIT",
            "CALL FROM 9863571861",
            "CONNECTION SUCCESSFUL",
        ]
        capacity = TEXT_ROW_CELLS * TEXT_ROWS

        # Seven of the eight fit, and one does not.
        fitting = [m for m in messages if len(m) <= capacity]
        assert len(fitting) == 7

        # The one that does not, recorded explicitly.
        overflow = [m for m in messages if len(m) > capacity]
        assert overflow == ["CONNECTION SUCCESSFUL"]
        assert len(overflow[0]) - capacity == 1

    def test_the_longest_fitting_cluster_message_fills_the_area_exactly(self):
        """`CALL FROM 9863571861` is 20 characters, which is exactly what the
        area holds. That the vendor's own longest message fills it to the
        character is the strongest evidence the measured width is right."""
        assert len("CALL FROM 9863571861") == TEXT_ROW_CELLS * TEXT_ROWS


# ---------------------------------------------------------------------------
# Outbound phone frames
# ---------------------------------------------------------------------------


class TestDecodePhoneFrames:
    def test_keepalive_has_no_checksum(self):
        """`returnMobilePacket`, `returnValidMobilePacket` and
        `returnMobilePacketWithACK` all write zero at index 18 and none calls
        the checksum routine."""
        raw = bytes.fromhex("5B4A0402000000020000000400000000000000FF")
        assert len(raw) == P.FRAME_SIZE
        assert raw[18] == 0x00
        frame = P.decode_phone_frame(raw)
        assert frame.kind == "mobile-data"
        assert frame.payload.network_type == 0x04
        assert not any("checksum" in n for n in frame.notes)

    def test_the_captured_keepalive_decodes(self):
        """The exact bytes the app sent on 2026-10-13, every 2.0 s:
        `5B 4A 04 02 00 00 00 02 00 00 00 04 00 00 00 00 00 00 00 FF`."""
        raw = bytes.fromhex("5B4A0402000000020000000400000000000000FF")
        assert len(raw) == P.FRAME_SIZE, P.hexdump(raw)
        frame = P.decode_phone_frame(raw)
        assert frame.kind == "mobile-data"
        assert frame.payload.signal_bars == 4
        assert frame.payload.battery_bars == 2
        assert frame.payload.network_type == 0x04
        assert frame.notes == []

    def test_nav_control_reports_both_byte_orders(self):
        """`PacketBuilder.putUInt16` writes little-endian while every field this
        simulator reads from the cluster is big-endian. The outbound order is
        not established on hardware, so both readings must be reported."""
        # The frame the app was observed sending, with distance 550 m.
        raw = bytes.fromhex("5A4E26029B0126020007010100000000000000FF")
        frame = P.decode_phone_frame(raw)
        assert frame.kind == "nav-control"

        little = P.get_u16(raw, 2, "little")
        assert little == 550

        big = P.get_u16(raw, 2, "big")
        assert big == 0x2602
        assert big != little

        # Both are on the record, and the ambiguity is stated.
        assert frame.payload.little.distance_m == 550
        assert frame.payload.big.distance_m == 0x2602
        assert any("byte order" in n or "endian" in n for n in frame.notes)

    def test_nav_text_decodes_ascii(self):
        """The captured frame: `5B 4F "toward Example Rd"`."""
        text = b"toward Example Rd"
        raw = bytes([0x5B, 0x4F]) + text.ljust(17, b"\x00") + bytes([0xFF])
        frame = P.decode_phone_frame(raw)
        assert frame.kind == "text"
        assert frame.payload.text == "toward Example Rd"
        assert len(frame.payload.text) == 17

    def test_seventeen_characters_is_flagged_as_at_the_row_limit(self):
        """The row is seventeen wide on the wire, which is *wider* than the
        cluster's ten cells. The note exists so the two facts sit together."""
        raw = bytes([0x5B, 0x4F]) + b"A" * 17 + bytes([0xFF])
        frame = P.decode_phone_frame(raw)
        assert any("17 characters" in n for n in frame.notes)

    def test_punctuation_survives_the_decode(self):
        """`+` and `-` are deliberately kept by the sanitiser: folding a dash
        turns NH-44 into NH 44, which is a different road."""
        raw = bytes([0x5B, 0x4F]) + b"NH-44 +91".ljust(17, b"\x00") + bytes([0xFF])
        frame = P.decode_phone_frame(raw)
        assert frame.payload.text == "NH-44 +91"
        assert any("unverified" in n for n in frame.notes)

    def test_non_ascii_is_reported_not_swallowed(self):
        raw = bytes([0x5B, 0x4F]) + "NH-44 தென்".encode()[:17].ljust(17, b"\x00")
        raw = raw + bytes([0xFF])
        frame = P.decode_phone_frame(raw)
        assert frame.kind == "text"
        assert any("outside printable ASCII" in n for n in frame.notes)

    def test_short_frame_does_not_raise(self):
        """A cluster that crashed on a malformed write would be useless for
        finding out how the phone behaves when it is unsure of itself."""
        assert P.decode_phone_frame(b"") is None
        assert P.decode_phone_frame(b"\x5b") is None
        assert P.decode_phone_frame(b"\x5b\x4f") is None

    def test_unknown_frame_is_reported_as_unknown(self):
        frame = P.decode_phone_frame(bytes([0x5B, 0x77]) + bytes(18))
        assert frame.kind == "unknown"

    def test_missing_terminator_is_reported(self):
        raw = bytes([0x5B, 0x4F]) + b"hi".ljust(17, b"\x00") + bytes([0x00])
        frame = P.decode_phone_frame(raw)
        assert any("0xFF terminator" in n for n in frame.notes)


# ---------------------------------------------------------------------------
# Frame 16
# ---------------------------------------------------------------------------


class TestFrame16:
    def test_default_reproduces_the_captured_bytes(self):
        """`5A 16` arrived 136 times in the capture and its layout is unknown
        anywhere. Reproducing the captured bytes exactly is the only claim
        available about it."""
        raw = P.encode_frame_16()
        # The quote covers 16 bytes; the frame is 20, so only those 16 are pinned and
        # the rest is zero-fill plus the terminator.
        captured = P.FRAME_16_CAPTURED
        assert P.FRAME_16_CAPTURED_LENGTH == 16
        assert raw[:16] == captured[:16]
        assert raw[16:19] == bytes(3)
        assert raw[:2] == bytes([0x5A, 0x16])
        assert raw[19] == 0xFF

    def test_fields_can_be_overridden_for_a_hypothesis(self):
        raw = P.encode_frame_16({"b7": 0xBEEF})
        assert P.get_u16(raw, 7, "big") == 0xBEEF
        # Fields not named are left alone.
        assert raw[2:7] == P.FRAME_16_CAPTURED[2:7]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_address_lands_in_bytes_seven_to_twelve(self):
        """`5B 42` carries the cluster's own BLE address at indices 7-12."""
        raw = P.encode_identity("5E:00:01:01:00:01")
        assert raw[7:13] == bytes([0x5E, 0x00, 0x01, 0x01, 0x00, 0x01])

    def test_a_bad_address_is_rejected(self):
        with pytest.raises(ValueError):
            P.encode_identity("5E:00:01")


# ---------------------------------------------------------------------------
# Challenge
# ---------------------------------------------------------------------------


class TestChallenge:
    def test_challenge_shape(self):
        """`9B F2` plus 16 random bytes, from `BlePacketsProvider.byteArrayForUnLock`."""
        raw = P.encode_challenge(bytes(range(16)))
        assert raw[0] == 0x9B
        assert raw[1] == 0xF2
        assert raw[2:18] == bytes(range(16))
        assert raw[19] == 0xFF

    def test_challenge_is_off_by_default(self):
        """The owner's cluster never sent one, in the whole session."""
        assert ClusterState().send_challenge is False
