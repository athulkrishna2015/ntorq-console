# Named states, so a particular situation can be reached in one command.
#
# Each scenario is a plain function on the state. They exist because the useful
# debugging question is usually "what does the app do when X", and X is a
# situation - a cold engine, a reserve tank, a phone that has just paired - not
# a value to type into six separate fields.
#
# Every scenario is a state someone can actually observe on the real vehicle,
# except where the docstring says otherwise. Scenarios that reproduce something
# the cluster demonstrably does *not* do are marked, and those are the ones
# worth running against the app.

from __future__ import annotations

from collections.abc import Callable

from .cluster import (
    FUEL_FULL_L,
    FUEL_HALF_L,
    FUEL_RESERVE_L,
    TEMP_IDLE_C,
    TEMP_NORMAL_C,
    TEMP_OVER_RANGE_C,
    ClusterState,
    Mode,
    SegIcon,
    Telltale,
    Trip,
)

Scenario = Callable[[ClusterState], None]


def cold_start(state: ClusterState) -> None:
    """Ignition just turned on, engine not yet started.

    The manual says the helmet icon blinks for ten seconds after every ignition
    ON, and that the malfunction lamp stays lit after the self-check until the
    engine starts. Both are on here.
    """
    state.ignition = True
    state.engine_running = False
    state.speed_kmh = 0.0
    state.ignition_at = state.now
    state.segment_icons.add(SegIcon.HELMET)
    state.telltales.add(Telltale.MIL)
    state.mode = Mode.STREET
    state.announce("FUEL EMPTY" if state.fuel_at_reserve() else state.mode.label)


def running(state: ClusterState) -> None:
    """Ordinary riding: warm engine, half a tank, phone connected."""
    state.ignition = True
    state.engine_running = True
    state.ignition_at = state.now - 60
    state.segment_icons.discard(SegIcon.HELMET)
    state.telltales.discard(Telltale.MIL)
    state.speed_kmh = 45.0
    state.throttle_percent = 40.0
    state.engine_temp_c = TEMP_NORMAL_C
    state.fuel_litres = FUEL_HALF_L
    state.top_speed_kmh = 78
    state.average_speed_kmh = 44
    state.mode = Mode.STREET
    state.selected_trip = Trip.A


def city_traffic(state: ClusterState) -> None:
    """Stop-start riding, which is where lap timers and the 2 km/h threshold
    are exercised. Manual 2a: the lap counter starts above 2 km/h and stops
    below it."""
    state.ignition = True
    state.engine_running = True
    state.speed_kmh = 6.0
    state.throttle_percent = 15.0
    state.mode = Mode.SPORT
    state.fuel_litres = 2.6
    state.lap_seconds = 41.0
    state.lap_distance_tenths = 340
    state.shortest_time_seconds = 8.4
    state.top_speed_kmh = 64
    state.selected_trip = Trip.B


def reserve_fuel(state: ClusterState) -> None:
    """Tank at the manual's minimum safe level, 1.2 litres.

    Manual 1c: the last bar blinks, the low-fuel lamp glows, `FUEL EMPTY`
    appears and TRIP F starts counting from 0.0.
    """
    state.ignition = True
    state.engine_running = True
    state.fuel_litres = FUEL_RESERVE_L
    state.telltales.add(Telltale.LOW_FUEL)
    state.selected_trip = Trip.F
    state.trip_f_tenths = 0
    state.speed_kmh = 12.0
    state.announce("FUEL EMPTY", ttl=999.0, kind="warning")


def full_tank(state: ClusterState) -> None:
    """Over five litres: all six bars, per the manual."""
    state.fuel_litres = FUEL_FULL_L
    state.telltales.discard(Telltale.LOW_FUEL)
    state.selected_trip = Trip.A


def overheating(state: ClusterState) -> None:
    """Past the over-range point.

    Manual 1e: all six temperature bars and `ENGINE WARNING`, after which the
    manual asks for ten minutes of idling.
    """
    state.engine_temp_c = TEMP_OVER_RANGE_C + 4
    state.speed_kmh = 0.0
    state.throttle_percent = 0.0
    state.announce("ENGINE WARNING", ttl=999.0, kind="warning")


def phone_paired(state: ClusterState) -> None:
    """Just connected. Manual: `CONNECTION SUCCESSFUL` for three seconds, the
    Bluetooth icon, the phone's battery level and its signal strength."""
    state.phone_connected = True
    state.phone_battery_bars = 4
    state.phone_signal_bars = 3
    state.segment_icons.update(
        {SegIcon.BLUETOOTH, SegIcon.PHONE_BATTERY, SegIcon.SIGNAL}
    )
    state.phone_name = "PHONE"
    state.announce("CONNECTION SUCCESSFUL", ttl=3.0, kind="link")


def incoming_call(state: ClusterState) -> None:
    """Manual: `CALL FROM <name>`, and the green smart-connect LED blinks
    until the call is active."""
    state.caller = "EXAMPLE"
    state.telltales.add(Telltale.NOTIFY)
    state.announce("CALL FROM EXAMPLE", ttl=999.0, kind="call")


def incoming_sms(state: ClusterState) -> None:
    """Manual: `SMS FROM <name>`, and the LED blinks for about five seconds.
    Only shown when the sender is in the contact list."""
    state.sms_from = "EXAMPLE"
    state.segment_icons.add(SegIcon.ENVELOPE)
    state.telltales.add(Telltale.NOTIFY)
    state.announce("SMS FROM EXAMPLE", ttl=999.0, kind="sms")


def missed_calls(state: ClusterState) -> None:
    """Manual: the total count from the phone, shown for about ten seconds."""
    state.caller = None
    state.missed_calls = 3
    state.announce("3 MISSED CALLS", ttl=10.0, kind="call")


def bt_pair_mode(state: ClusterState) -> None:
    """Manual: the cluster enters this on a battery disconnect or a blown fuse,
    and the Bluetooth name becomes visible."""
    state.mode = Mode.BT_PAIR
    state.speed_kmh = 0.0
    state.engine_running = False
    state.announce("BT PAIR", ttl=999.0, kind="mode")


def side_stand_down(state: ClusterState) -> None:
    """Manual 1m: the stand indicator lights and the bike will not start."""
    state.side_stand = True
    state.segment_icons.add(SegIcon.SIDE_STAND)
    state.engine_running = False
    state.speed_kmh = 0.0


def service_due(state: ClusterState) -> None:
    """Manual 1h: the spanner blinks for ten seconds after ignition, then
    glows until the reminder is reset."""
    state.segment_icons.add(SegIcon.SERVICE)


def park_and_lock(state: ClusterState) -> None:
    """Ignition off. The cluster keeps the odometer and the clock and drops
    everything else, which is the state a scan finds it in."""
    state.ignition = False
    state.engine_running = False
    state.speed_kmh = 0.0
    state.throttle_percent = 0.0
    state.engine_temp_c = TEMP_IDLE_C
    state.segment_icons.clear()
    state.telltales.discard(Telltale.MIL)
    state.message = None
    state.phone_connected = False
    state.nav.active = False
    state.nav.text = ""


def navigation_active(state: ClusterState) -> None:
    """A route being mirrored, as the phone would present it.

    The cluster's own state is unchanged; only what the phone has told it is
    set. Use it after `phone_paired` to see the HUD populated.
    """
    from . import protocol as P

    state.nav.active = True
    state.nav.pictogram = 0
    state.nav.distance_m = 550
    state.nav.eta_minutes = 18
    state.nav.remaining_m = 12_500
    state.nav.text = "Example Road"
    state.apply_phone_frame(
        P.PhoneFrame(
            "text",
            b"",
            P.PhoneDataId.NAVIGATION_TEXT,
            P.TextRow("Example Road", P.PhoneDataId.NAVIGATION_TEXT),
            [],
        )
    )


#: Scenarios the page offers, by name.
SCENARIOS: dict[str, Scenario] = {
    "cold-start": cold_start,
    "running": running,
    "city-traffic": city_traffic,
    "reserve-fuel": reserve_fuel,
    "full-tank": full_tank,
    "overheating": overheating,
    "phone-paired": phone_paired,
    "incoming-call": incoming_call,
    "incoming-sms": incoming_sms,
    "missed-calls": missed_calls,
    "bt-pair-mode": bt_pair_mode,
    "side-stand-down": side_stand_down,
    "service-due": service_due,
    "park-and-lock": park_and_lock,
    "navigation-active": navigation_active,
}
