"""Tests for the running cluster: ticks, frame cadence, and the phone link.

These exercise the parts that a static render cannot reach - that frames arrive
on the right schedule, that a malformed write does not take the simulator down,
and that the display state actually follows from the frames rather than being
set separately. A simulator whose display and whose wire format disagreed would
be worse than no simulator at all, because it would confirm a bug in both.
"""

from __future__ import annotations

import pytest

from ntorq import protocol as P
from ntorq.cluster import ClusterState, Mode, Trip
from ntorq.sim import (
    PERIOD_FRAME_10,
    PERIOD_KEEPALIVE,
    TICK_HZ,
    TICK_INTERVAL,
    SimulatedCluster,
    iter_frames,
    replay,
)


def _cluster(**kwargs) -> SimulatedCluster:
    state = ClusterState(odometer_tenths=165_930)
    return SimulatedCluster(state=state, **kwargs)


class TestCadence:
    def test_tick_rate_matches_the_clusters_own_stream(self):
        """docs/PROTOCOL.md section 7: the baseline cadence is 20 ms, which is
        what the silence-gap detection is measured against."""
        assert TICK_HZ == 50
        assert TICK_INTERVAL == pytest.approx(0.02)

    def test_ticking_advances_time_by_exactly_the_interval(self):
        cluster = _cluster()
        before = cluster.clock
        cluster.run_for(2.0)
        assert cluster.clock - before == pytest.approx(2.0, abs=0.01)

    def test_run_for_is_deterministic(self):
        """Two runs of the same length must produce the same frames. A
        simulator whose output varied between runs could not be used to
        reproduce a bug."""
        first = [f for f in iter_frames(_cluster(), 30.0)]
        second = [f for f in iter_frames(_cluster(), 30.0)]
        assert first == second

    def test_speedometer_1_arrives_at_its_stated_rate(self):
        cluster = _cluster()
        seconds = 30.0
        frames = list(iter_frames(cluster, seconds))
        count = sum(1 for f in frames if f[:2] == bytes([0x5A, 0x10]))
        expected = seconds / PERIOD_FRAME_10
        # Tolerance is one frame on each side: the schedule is evaluated per
        # tick, so a period of 0.9 s lands on a whole number of ticks and the
        # count can only be a floor or a ceiling of the ideal.
        assert abs(count - expected) <= 1.5, f"{count} frames, expected ~{expected:.1f}"

    def test_frame_16_arrives_as_often_as_the_speedometer(self):
        """`5A 16` was the second most common frame in the capture: 136 against
        the speedometer's 132. It must not be an afterthought here."""
        cluster = _cluster()
        frames = list(iter_frames(cluster, 60.0))
        sixteen = sum(1 for f in frames if f[:2] == bytes([0x5A, 0x16]))
        ten = sum(1 for f in frames if f[:2] == bytes([0x5A, 0x10]))
        assert 0.9 < sixteen / ten < 1.1, f"16: {sixteen}, 10: {ten}"

    def test_frame_types_do_not_arrive_in_a_burst(self):
        """The four periodic frames are staggered in `__post_init__` so they do
        not all come due on the same tick. A real cluster does not send four
        frames in the same 20 ms."""
        cluster = _cluster()
        cluster._startup_frames_left = 0  # skip the connect-time zeros
        per_tick = [len(cluster.due_frames()) for _ in range(4000)]
        assert max(per_tick) <= 2, f"{max(per_tick)} frames on one tick"

    def test_keepalive_period_is_two_seconds(self):
        """The app's keepalive went out exactly every 2.0 s in the capture."""
        assert PERIOD_KEEPALIVE == 2.0


class TestStartUpFrames:
    def test_all_zero_frames_arrive_before_real_telemetry(self):
        """The capture has them: "A few all-zero 20-byte frames arrive at
        connect time, before real telemetry starts - worth knowing so they are
        not mistaken for decode failures." """
        cluster = _cluster()
        assert cluster.emit_startup_zero_frames

        first = cluster.poll()
        assert first == [bytes(P.FRAME_SIZE)]

        # They stop after a few.
        seen_zero = 0
        for _ in range(400):
            for raw in cluster.poll():
                if raw == bytes(P.FRAME_SIZE):
                    seen_zero += 1
        assert 1 <= seen_zero <= 5

    def test_startup_frames_can_be_turned_off(self):
        cluster = _cluster(emit_startup_zero_frames=False)
        # Nothing all-zero, ever - the first frame must already be real.
        for _ in range(200):
            for raw in cluster.poll():
                assert raw != bytes(P.FRAME_SIZE)

    def test_the_first_real_frame_is_the_speedometer(self):
        """Once the connect-time zeros are out of the way, `5A 10` leads: it is
        scheduled first and has the shortest period, and it is the frame the
        dashboard waits on."""
        cluster = _cluster(emit_startup_zero_frames=False)
        first_real = None
        for _ in range(200):
            for raw in cluster.poll():
                if raw[:2] == bytes([0x5A, 0x10]):
                    first_real = raw
                    break
            if first_real:
                break
        assert first_real is not None, "no 5A 10 frame in the first 200 ticks"
        assert len(first_real) == P.FRAME_SIZE

    def test_a_zero_frame_is_not_a_decode_failure(self):
        """It must decode without raising, and must not produce a reading."""
        frame = P.decode_phone_frame(bytes(P.FRAME_SIZE))
        assert frame is not None
        assert frame.kind == "unknown"


class TestFrame19OffByDefault:
    def test_frame_19_is_not_sent_unless_asked_for(self):
        """This cluster never sent `5A 19`, despite it being in the frame table.
        Emitting it by default would be sending a frame the real vehicle does
        not send, and the app's tiles would light up from it."""
        cluster = _cluster()
        frames = list(iter_frames(cluster, 120.0))
        assert not any(f[:2] == bytes([0x5A, 0x19]) for f in frames)

    def test_frame_19_can_be_switched_on(self):
        """And it can be, which is how you check the app copes with a frame the
        cluster never sends."""
        cluster = _cluster()
        cluster.state.emit_frame_19 = True
        cluster._scheduled["19"] = cluster.clock
        frames = list(iter_frames(cluster, 120.0))
        assert any(f[:2] == bytes([0x5A, 0x19]) for f in frames)


class TestPhoneLink:
    def test_connecting_raises_connection_successful(self):
        """Manual: 'CONNECTION SUCCESSFUL' for three seconds."""
        cluster = _cluster()
        cluster.connect()
        assert cluster.state.message.text == "CONNECTION SUCCESSFUL"
        assert cluster.state.message.until == pytest.approx(3.0)

    def test_connecting_adds_the_phones_icons(self):
        """Manual: the Bluetooth icon, the phone's battery level and its signal
        strength are all displayed."""
        from ntorq.cluster import SegIcon

        cluster = _cluster()
        cluster.connect()
        assert SegIcon.BLUETOOTH in cluster.state.segment_icons
        assert SegIcon.PHONE_BATTERY in cluster.state.segment_icons
        assert SegIcon.SIGNAL in cluster.state.segment_icons

    def test_disconnecting_names_the_phone(self):
        """Manual: "Ex: 'JOHN DISCONNECTED' message will be displayed"."""
        cluster = _cluster()
        cluster.connect(name="EXAMPLE")
        cluster.disconnect()
        assert cluster.state.message.text == "EXAMPLE DISCONNECTED"
        assert cluster.state.phone_connected is False

    def test_disconnecting_clears_the_navigation(self):
        """A HUD that outlived the link would show a stale route on a parked
        scooter."""
        cluster = _cluster()
        cluster.connect()
        cluster.on_phone_frame(
            bytes.fromhex("5A4E26029B0126020007010100000000000000FF")
        )
        assert cluster.state.nav.active
        cluster.disconnect()
        assert not cluster.state.nav.active
        assert cluster.state.nav.text == ""

    def test_messages_expire(self):
        cluster = _cluster()
        cluster.connect()
        cluster.run_for(4.0)
        assert cluster.state.message is None


class TestPhoneFramesDriveTheDisplay:
    def test_nav_control_sets_the_hud(self):
        cluster = _cluster()
        cluster.connect()
        cluster.on_phone_frame(
            bytes.fromhex("5A4E26029B0126020007010100000000000000FF")
        )
        nav = cluster.state.nav
        assert nav.active
        # 550 m little-endian is the app's own encoding; the cluster does not
        # know which order was meant, so both are recorded.
        assert nav.pictogram == 7
        assert nav.rows == 1
        assert nav.distance_m in (550, 0x2602)

    def test_nav_text_sets_the_street(self):
        cluster = _cluster()
        cluster.connect()
        raw = bytes([0x5B, 0x4F]) + b"Onto Example Road".ljust(17, b"\x00") + bytes([0xFF])
        cluster.on_phone_frame(raw)
        assert cluster.state.nav.text == "Onto Example Road"

    def test_a_message_row_does_not_become_the_street_name(self):
        """`0x5B 0x4C` is a caller or SMS row. The cluster tells them apart by
        data id, so a notification must not be able to overwrite the HUD."""
        cluster = _cluster()
        cluster.connect()
        cluster.on_phone_frame(
            bytes([0x5B, 0x4F]) + b"Example Road".ljust(17, b"\x00") + bytes([0xFF])
        )
        cluster.on_phone_frame(
            bytes([0x5B, 0x4C]) + b"CALL FROM AMIT".ljust(17, b"\x00") + bytes([0xFF])
        )
        assert cluster.state.nav.text == "Example Road"
        assert cluster.state.message.text == "CALL FROM AMIT"

    def test_keepalive_sets_the_phone_icons(self):
        cluster = _cluster()
        cluster.connect()
        cluster.on_phone_frame(
            bytes.fromhex("5B4A0402000000020000000400000000000000FF")
        )
        assert cluster.state.phone_signal_bars == 4
        assert cluster.state.phone_battery_bars == 2

    def test_find_my_flag_is_reported_because_the_official_packet_has_none(self):
        """The flag lives at byte 17, which the official builder leaves zero."""
        cluster = _cluster()
        cluster.connect()
        raw = bytearray(bytes.fromhex("5B4A0402000000020000000400000000000000FF"))
        raw[17] = 0x01
        frame = cluster.on_phone_frame(bytes(raw))
        assert frame.kind == "mobile-data"
        assert any("find-my" in n for n in cluster.state.nav.notes)

    def test_every_decoded_note_reaches_the_display_state(self):
        """A warning raised while decoding a frame that never appears anywhere
        is a warning that never gets read."""
        cluster = _cluster()
        cluster.connect()
        cluster.on_phone_frame(
            bytes.fromhex("5A4E26029B0126020007010100000000000000FF")
        )
        assert cluster.state.nav.notes
        assert any("byte order" in n for n in cluster.state.nav.notes)

    def test_a_malformed_write_does_not_take_the_cluster_down(self):
        """The whole point of a simulator is to find out how the app behaves
        when it is not sure what it is talking to."""
        cluster = _cluster()
        cluster.connect()
        for junk in (b"", b"\x5b", b"\xff" * 3, bytes(range(20)), b"\x00" * 40):
            cluster.on_phone_frame(junk)
        # Still alive and still producing frames.
        assert any(iter_frames(cluster, 5.0))


class TestLog:
    def test_both_directions_are_logged(self):
        cluster = _cluster()
        cluster.connect()
        for raw in cluster.poll():
            cluster.record_out(raw)
        cluster.on_phone_frame(
            bytes.fromhex("5B4A0402000000020000000400000000000000FF")
        )
        directions = {record.direction.value for record in cluster.log}
        assert directions == {"out", "in"}

    def test_log_is_bounded(self):
        """The phone keeps a 4000-entry ring; matching it means the two logs
        can be read side by side."""
        cluster = _cluster()
        for _ in range(300):
            for raw in cluster.poll():
                cluster.record_out(raw)
        assert len(cluster.log) <= 4000

    def test_replay_is_readable(self):
        cluster = _cluster()
        for raw in cluster.poll():
            cluster.record_out(raw)
        text = replay(cluster.recent(5))
        assert text
        for line in text.splitlines():
            assert line.strip()


class TestTicks:
    def test_odometer_advances_only_while_moving(self):
        cluster = _cluster()
        start = cluster.state.odometer_tenths
        cluster.state.speed_kmh = 0.0
        cluster.run_for(10.0)
        assert cluster.state.odometer_tenths == start

        cluster.state.speed_kmh = 60.0
        cluster.run_for(10.0)
        # 60 km/h for 10 s is 0.1667 km.
        assert cluster.state.odometer_tenths > start

    def test_fuel_burns_and_never_goes_negative(self):
        cluster = _cluster()
        cluster.state.speed_kmh = 80.0
        cluster.state.throttle_percent = 60.0
        before = cluster.state.fuel_litres
        cluster.run_for(60.0)
        assert cluster.state.fuel_litres < before
        cluster.state.fuel_litres = 0.001
        cluster.run_for(600.0)
        assert cluster.state.fuel_litres >= 0.0

    def test_lap_timer_starts_above_two_and_stops_below(self):
        """Manual 2a, verbatim: "once the vehicle speed crosses 2 km/h, the lap
        distance counter starts counting ... and stops when the speed goes below
        2 km/h"."""
        cluster = _cluster()
        cluster.state.mode = Mode.SPORT
        cluster.state.speed_kmh = 0.0
        cluster.run_for(1.0)

        cluster.state.speed_kmh = 30.0
        cluster.run_for(4.0)
        assert cluster.state.lap_seconds > 0

        cluster.state.speed_kmh = 0.0
        cluster.run_for(1.0)
        stopped_at = cluster.state.lap_seconds
        cluster.run_for(5.0)
        assert cluster.state.lap_seconds == stopped_at

    def test_zero_to_sixty_clears_when_the_bike_stops(self):
        """Manual 2c: the value disappears if speed drops below 2 km/h."""
        cluster = _cluster()
        cluster.state.speed_kmh = 0.0
        cluster.run_for(1.0)
        # Accelerate through 60 rather than jumping to it: a speed that goes
        # from 0 to 65 between two ticks crosses both thresholds at once, which
        # is not a measurable run.
        for speed in (5.0, 20.0, 40.0, 65.0):
            cluster.state.speed_kmh = speed
            cluster.run_for(0.5)
        assert cluster.state.zero_to_sixty_seconds > 0

        cluster.state.speed_kmh = 0.0
        cluster.run_for(1.0)
        assert cluster.state.zero_to_sixty_seconds == 0.0

    def test_a_speed_that_jumps_past_sixty_records_no_run(self):
        """An instantaneous 0 to 65 km/h is not a 0-60 time, and recording
        `0.0` would put a plausible figure on the dash for something that never
        happened."""
        cluster = _cluster()
        cluster.state.speed_kmh = 0.0
        cluster.run_for(1.0)
        cluster.state.speed_kmh = 65.0
        cluster.run_for(0.5)
        assert cluster.state.zero_to_sixty_seconds == 0.0

    def test_non_riding_modes_hand_back_to_street_above_two_kmh(self):
        """Manual: "In this mode, if the vehicle speed crosses above 2 km/h,
        the connected instrument cluster will enter to 'STREET MODE'
        automatically"."""
        for mode in (Mode.RIDE_STATS_LAST, Mode.BT_PAIR, Mode.SET_CLOCK):
            cluster = _cluster()
            cluster.state.mode = mode
            cluster.state.speed_kmh = 0.0
            cluster.run_for(1.0)
            assert cluster.state.mode == mode

            cluster.state.speed_kmh = 10.0
            cluster.run_for(1.0)
            assert cluster.state.mode == Mode.STREET, mode

    def test_power_lamp_blinks_after_twenty_seconds_of_idle(self):
        from ntorq.cluster import Telltale

        cluster = _cluster()
        cluster.state.speed_kmh = 0.0
        cluster.state.ignition = True
        cluster.run_for(10.0)
        assert Telltale.POWER not in cluster.state.telltales

        cluster.run_for(20.0)
        assert Telltale.POWER in cluster.state.telltales


class TestFuelBehaviour:
    def test_reserve_fuel_switches_to_trip_f(self):
        """Manual 1k: TRIP F appears on its own at reserve and counts from 0.0."""
        cluster = _cluster()
        cluster.state.selected_trip = Trip.A
        cluster.state.fuel_litres = 0.5
        cluster.run_for(1.0)
        assert cluster.state.selected_trip == Trip.F
        assert cluster.state.message.text == "FUEL EMPTY"

    def test_refuelling_restores_trip_a_and_clears_trip_f(self):
        cluster = _cluster()
        cluster.state.fuel_litres = 0.5
        cluster.run_for(1.0)
        assert cluster.state.selected_trip == Trip.F

        cluster.state.fuel_litres = 3.2
        cluster.run_for(1.0)
        assert cluster.state.selected_trip == Trip.A
        assert cluster.state.trip_f_tenths == 0

    def test_overheating_takes_priority_over_navigation(self):
        """Manual 1e asks for ten minutes of idling. A navigation message that
        could cover the warning up would be a defect."""
        cluster = _cluster()
        cluster.connect()
        # Past the three seconds `CONNECTION SUCCESSFUL` lasts, so the display
        # is genuinely free to show the navigation text.
        cluster.run_for(4.0)
        # Both halves of a route: the control frame makes the HUD active, the
        # text frame carries the street. The app sends them on a short cycle.
        cluster.on_phone_frame(
            bytes.fromhex("5A4E26029B0126020007010100000000000000FF")
        )
        cluster.on_phone_frame(
            bytes([0x5B, 0x4F]) + b"Example Road".ljust(17, b"\x00") + bytes([0xFF])
        )
        assert cluster.state.message.text == "Example Road"

        cluster.state.engine_temp_c = 120
        cluster.run_for(0.1)
        assert cluster.state.message.text == "ENGINE WARNING"

    def test_low_fuel_lamp_follows_the_gauge(self):
        from ntorq.cluster import Telltale

        cluster = _cluster()
        cluster.state.fuel_litres = 3.2
        cluster.run_for(1.0)
        assert Telltale.LOW_FUEL not in cluster.state.telltales

        cluster.state.fuel_litres = 1.0
        cluster.run_for(1.0)
        assert Telltale.LOW_FUEL in cluster.state.telltales


class TestFaultInjection:
    @pytest.mark.parametrize("name", ClusterState.FAULTS)
    def test_every_advertised_fault_applies(self, name):
        cluster = _cluster()
        cluster.state.inject(name)

    def test_raw_fuel_7_is_reachable_by_name(self):
        """The value the cluster actually sent on an empty tank."""
        cluster = _cluster()
        cluster.state.inject("raw-fuel-7")
        raw = P.encode_speedometer1(cluster.state.speedometer_1())
        assert raw[6] == 7

    def test_clear_faults_restores_a_known_state(self):
        cluster = _cluster()
        cluster.state.inject("low-fuel")
        cluster.state.inject("mil")
        cluster.state.clear_faults()
        assert not cluster.state.telltales
        assert cluster.state.fuel_litres == pytest.approx(3.2)

    def test_an_unknown_fault_is_rejected_by_name(self):
        with pytest.raises(KeyError):
            _cluster().state.inject("no-such-fault")


class TestSnapshot:
    def test_snapshot_is_json_serialisable(self):
        """The web display serialises it directly, so anything unserialisable
        here is a 500 on the page."""
        import json

        cluster = _cluster()
        cluster.connect()
        for raw in cluster.poll():
            cluster.record_out(raw)
        json.dumps(cluster.snapshot())

    def test_snapshot_carries_what_the_display_needs(self):
        cluster = _cluster()
        snapshot = cluster.snapshot()
        for key in ("clock", "speedKmh", "odometerKm", "fuelBars", "nav", "frames"):
            assert key in snapshot

    def test_snapshot_reflects_the_state_after_a_phone_frame(self):
        cluster = _cluster()
        cluster.connect()
        cluster.on_phone_frame(
            bytes.fromhex("5A4E26029B0126020007010100000000000000FF")
        )
        assert cluster.snapshot()["nav"]["active"] is True
