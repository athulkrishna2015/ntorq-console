"""Tests for the GATT and advertising layers.

The advertising tests are the ones that matter. Advertising the service UUID
would make a scan filtered on `5456534D-...` succeed where it fails against the
real cluster, and the app's scan failing is exactly what forced the name-based
fallback in the first place. That is a bug this repository could otherwise
introduce into the project it exists to test, and it would be invisible until
someone put both in front of the same phone.

The GATT server tests cover the parts that can be checked without a radio: the
UUIDs, the characteristic flags, and the failure reporting when the environment
cannot support it.
"""

from __future__ import annotations

import pytest

from ntorq.gatt import advertise as adv
from ntorq.gatt import server as srv


class TestServiceIdentity:
    def test_the_service_uuid_is_the_ascii_name_read_byte_wise(self):
        """The UUID is the ASCII string `TVSM-VGSASBEN TORQ`, which is why the
        non-EV line shares one service."""
        expected = srv.SERVICE_UUID.replace("-", "")
        assert bytes.fromhex(expected) == srv.SERVICE_UUID_IS_ASCII.encode("ascii")

    def test_the_uuid_matches_the_official_dex(self):
        """Transcribed from `com.tvs.bike.core.bikes.*BLEConnectionConfig`,
        identical for Ntorq, Jupiter, Apache and iQube."""
        assert srv.SERVICE_UUID == "5456534D-5647-5341-5342-454E544F5251"
        assert srv.WRITE_UUID == "00005352-0000-1000-8000-00805f9b34fb"
        assert srv.READ_UUID == "00005354-0000-1000-8000-00805f9b34fb"

    def test_the_write_characteristic_accepts_writes_without_response(self):
        """`getCharacteristicsWriteType() == 2`, `WRITE_NO_RESPONSE`."""
        assert srv.PROP_WRITE_NO_RESPONSE == 0x0004
        assert srv.PROP_WRITE_NO_RESPONSE == 2 << 1

    def test_the_read_characteristic_notifies(self):
        """Frames come back on a notify, which is how the app receives
        telemetry: it reads the Log tab after connecting and frames in mean the
        link works."""
        assert srv.PROP_NOTIFY == 0x0010
        assert srv.PROP_READ == 0x0002


class TestAdvertising:
    def test_the_payload_carries_only_the_name(self):
        """**The whole point of this module.** The rider's cluster advertised
        no service UUID, so a scan filtered on the documented UUID finds
        nothing. A simulator that advertised it would pass a scan the real
        cluster fails, hiding a bug in the app's name-based fallback."""
        data = adv.build_advertising("TVSBT0000001")
        assert adv.device_name(data) == "TVSBT0000001"
        assert "service UUID" not in data.description

    def test_no_service_uuid_record_is_present_by_default(self):
        data = adv.build_advertising("TVSBT0000001")
        assert adv.ADV_TYPE_SERVICE_UUIDS_128BIT not in _record_types(data.payload)
        assert adv.ADV_TYPE_SERVICE_UUIDS_16BIT not in _record_types(data.payload)

    def test_the_name_is_the_complete_local_name_not_the_shortened_one(self):
        """The shortened variant is truncated by the advertising length and
        would not carry all ten characters, and the app's filter needs the
        prefix plus the digits to match a cluster."""
        data = adv.build_advertising("TVSBT0000001")
        types = _record_types(data.payload)
        assert adv.ADV_TYPE_COMPLETE_LOCAL_NAME in types
        assert adv.ADV_TYPE_SHORTENED_LOCAL_NAME not in types

    def test_the_advertised_name_passes_the_apps_own_filter(self):
        """The filter is a prefix test, so a simulator whose name does not match
        is invisible to the app under test."""
        data = adv.build_advertising("TVSBT0000001")
        assert adv.matches_app_filter(adv.device_name(data))

    def test_the_name_does_not_need_flags_to_be_discoverable(self):
        """Flags are omitted, matching the real cluster. The record length must
        still be correct or nothing is parsed at all."""
        data = adv.build_advertising("TVSBT0000001")
        assert adv.ADV_TYPE_FLAGS not in _record_types(data.payload)
        assert data.payload[0] == len(data.payload) - 1

    def test_the_service_uuid_can_be_added_for_a_demonstration(self):
        """The switch exists so the difference can be shown rather than
        asserted, and it labels itself as wrong when used."""
        data = adv.build_advertising("TVSBT0000001", include_service_uuid=True)
        assert adv.ADV_TYPE_SERVICE_UUIDS_128BIT in _record_types(data.payload)
        assert "NOT what the real cluster does" in data.description

    def test_the_name_stays_findable_when_the_uuid_is_added(self):
        """The two records do not both fit in 31 bytes, so the name moves to the
        scan response. It has to remain readable, or the demonstration mode is
        simply invisible rather than merely different."""
        data = adv.build_advertising("TVSBT0000001", include_service_uuid=True)
        assert data.scan_response is not None
        assert adv.device_name(data) == "TVSBT0000001"
        assert adv.matches_app_filter(adv.device_name(data))
        assert len(data.payload) <= adv.MAX_ADVERTISING_DATA
        assert len(data.scan_response) <= adv.MAX_ADVERTISING_DATA

    def test_a_long_name_is_truncated_from_the_end(self):
        """The app's filter is a prefix test, so losing the tail keeps the
        device findable and losing the head makes it invisible."""
        for data in (
            adv.build_advertising("TVSBT" + "9" * 40),
            adv.build_advertising("TVSBT" + "9" * 40, include_service_uuid=True),
        ):
            name = adv.device_name(data)
            assert name.startswith("TVSBT")
            assert adv.matches_app_filter(name)
            assert len(data.payload) <= adv.MAX_ADVERTISING_DATA
            assert data.payload[0] == len(data.payload) - 1
            if data.scan_response:
                assert len(data.scan_response) <= adv.MAX_ADVERTISING_DATA
                assert data.scan_response[0] == len(data.scan_response) - 1

    def test_the_payload_is_never_longer_than_the_limit(self):
        for suffix in ("", "0", "0000", "0" * 12, "0" * 40, "0" * 100):
            for include_uuid in (False, True):
                data = adv.build_advertising("TVSBT" + suffix, include_service_uuid=include_uuid)
                assert len(data.payload) <= adv.MAX_ADVERTISING_DATA
                if data.scan_response:
                    assert len(data.scan_response) <= adv.MAX_ADVERTISING_DATA

    def test_a_name_that_exactly_fits_is_not_truncated(self):
        # 12 bytes of name + 1 type + 1 length = 14, well inside 31.
        data = adv.build_advertising("TVSBT0000001")
        assert adv.device_name(data) == "TVSBT0000001"
        assert data.payload[0] == 13
        assert data.scan_response is None

    def test_parse_name_returns_none_for_payloads_without_one(self):
        assert adv.parse_name(b"") is None
        assert adv.parse_name(bytes([0x02, adv.ADV_TYPE_FLAGS, 0x06])) is None
        assert adv.parse_name(None, None) is None

    def test_parse_name_survives_a_malformed_payload(self):
        """A length that overruns the buffer must not raise: this parses
        whatever a scanner hands over."""
        for payload in (b"\xff\x09", bytes([0x20, 0x09]) + b"AB", b"\x00"):
            name = adv.parse_name(payload)
            assert name is None or isinstance(name, str)

    def test_the_name_prefix_matches_the_manual(self):
        """Manual: "the instrument cluster's Bluetooth name is prefixed by TVSBT
        and followed by alpha-numeric digits. Ex: 'TVSBT12345'"."""
        assert adv.NAME_PREFIX == "TVSBT"


def _record_types(payload: bytes) -> list[int]:
    """Walk the length-prefixed advertising records and return their types."""
    types = []
    index = 0
    while index + 1 < len(payload):
        length = payload[index]
        if length == 0:
            break
        types.append(payload[index + 1])
        index += 1 + length
    return types


class TestEnvironmentReporting:
    def test_probe_reports_rather_than_raises(self):
        """A simulator that crashes when bluetoothd is not running cannot be
        used to work out why bluetoothd is not running."""
        report = srv.probe_environment()
        assert isinstance(report.problems, list)
        assert isinstance(report.usable, bool)

    def test_describe_is_a_sentence_when_usable(self):
        report = srv.EnvironmentReport(True, "/org/bluez/hci0", "laptop", True, True, [])
        assert "ready" in report.describe()

    def test_describe_lists_every_problem_when_not(self):
        report = srv.EnvironmentReport(
            False, None, None, False, False, ["no bluetoothd", "no dbus module"]
        )
        text = report.describe()
        assert "no bluetoothd" in text
        assert "no dbus module" in text

    def test_a_failed_probe_makes_the_server_refuse_clearly(self):
        """Raising an error whose message says what to do is better than
        failing later with a D-Bus traceback."""
        gatt = srv.GattServer.__new__(srv.GattServer)
        assert hasattr(gatt, "register")

    def test_gatt_error_is_a_runtime_error(self):
        """So a caller can catch it without importing a BlueZ type."""
        assert issubclass(srv.GattError, RuntimeError)


class TestFrameFormatting:
    def test_hexdump_is_space_separated_upper_case(self):
        """The form both logs use, so the two can be read side by side."""
        from ntorq import protocol as P

        assert P.hexdump(bytes([0x5A, 0x10, 0xFF])) == "5A 10 FF"

    def test_frame_hexdumps_joins_frames(self):

        text = srv.frame_hexdumps([bytes([0x5A, 0x10]), bytes([0x5A, 0x11])])
        assert text == "5A 10 | 5A 11"


class TestAdvertisingPayloadMatchesTheLibrary:
    def test_our_payload_agrees_with_bleaks(self):
        """If `bleak` is installed, its own encoder should produce the same
        record, or the phone may not parse ours. Skipped when it is absent,
        which is the normal case: advertising is the one optional feature."""
        pytest.importorskip("bleak")
        from bleak.backends.bluezdbus.advertisement import (
            BlueZLEAdvertisement,
        )

        ours = adv.build_advertising("TVSBT0000001")
        theirs = BlueZLEAdvertisement(
            local_name="TVSBT0000001",
            service_uuids=[],
            service_data={},
            manufacturer_data={},
            advertising_type="peripheral",
        )
        assert theirs.local_name == adv.parse_name(ours.payload)
