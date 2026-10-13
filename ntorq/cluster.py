# The cluster's own state, and what it would be showing.
#
# Every rule here comes from TVS's own Ntorq 125 owner manual
# (`TVS NTORQ - Connected cluster.pdf`, pages 24-43) or from the capture
# recorded in TVSRideCompanion/docs/PROTOCOL.md section 4a. Where a value is
# an interpolation rather than a manual statement, it says so.
#
# The manual is unusually specific about the display, which is what makes a
# faithful clone possible at all: it enumerates every element, states how many
# bars each gauge has, names every message the alpha-numeric display shows,
# and gives litre thresholds for the fuel gauge.

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import StrEnum

from . import protocol as P

# ---------------------------------------------------------------------------
# Display geometry, measured off the manual's all-segments-on illustration
# ---------------------------------------------------------------------------

#: Character cells on the two text lines, measured independently from the
#: manual illustration and supplied by the project owner: line 1 has ten,
#: line 2 has twelve. Do not model this as a uniform grid.
TEXT_ROW_CAPACITIES = (10, 12)
TEXT_ROWS = len(TEXT_ROW_CAPACITIES)
#: Compatibility/name for the first-line capacity, which is the narrow line.
TEXT_ROW_CELLS = TEXT_ROW_CAPACITIES[0]

#: Total character capacity across both unequal lines.
TEXT_AREA_CAPACITY = sum(TEXT_ROW_CAPACITIES)

#: Bars in the fuel gauge and the engine-temperature gauge. The manual states
#: six for each, in so many words, twice.
GAUGE_BARS = 6

#: Litres at which the manual says each thing happens.
FUEL_FULL_L = 5.0
FUEL_HALF_L = 3.2
FUEL_RESERVE_L = 1.2

#: The manual gives no degrees, only "raises beyond normal operating
#: temperature". These are the simulator's reading of that phrase.
TEMP_IDLE_C = 25
TEMP_NORMAL_C = 85
TEMP_OVER_RANGE_C = 110

#: Speed at which lap timers start and stop, and above which the non-riding
#: modes hand control back to street mode. Manual 2a: "once the vehicle speed
#: crosses 2 km/h ... and stops when the speed goes below 2 km/h".
LAP_SPEED_THRESHOLD_KMH = 2.0

#: The 0-60 indicator only shows a real time below this; above it the cluster
#: displays 15 seconds. Manual 2c.
SHORTEST_TIME_DISPLAY_CAP_S = 15.0

#: How long the helmet icon blinks after every ignition ON, and how long the
#: service reminder blinks before it settles into a steady glow. Manual 1f, 1h.
BLINK_AFTER_IGNITION_S = 10.0

#: How long the engine may idle before the power lamp starts blinking.
IDLE_POWER_BLINK_S = 20.0

#: How long 'CONNECTION SUCCESSFUL' stays up. Manual: "for 3 secs".
CONNECTED_MESSAGE_S = 3.0

#: How long the missed-call count stays up. Manual: "10 seconds approx".
MISSED_CALL_MESSAGE_S = 10.0


class Mode(StrEnum):
    """The cluster's display modes. Manual, 'MODE AND SET BUTTONS'."""

    STREET = "street"
    SPORT = "sport"
    RIDE_STATS_LAST = "ride-stats-last"
    RIDE_STATS_BEST = "ride-stats-best"
    BT_PAIR = "bt-pair"
    SET_CLOCK = "set-clock"

    @property
    def label(self) -> str:
        return {
            Mode.STREET: "STREET MODE",
            Mode.SPORT: "SPORT MODE",
            Mode.RIDE_STATS_LAST: "RIDE STATS LAST LAP",
            Mode.RIDE_STATS_BEST: "RIDE STATS BEST LAP",
            Mode.BT_PAIR: "BT PAIR",
            Mode.SET_CLOCK: "SET CLOCK",
        }[self]


class Trip(StrEnum):
    ODO = "odo"
    A = "a"
    B = "b"
    F = "f"


#: `5A 10` index 8, SELECTED_TRIP.
TRIP_STATUS_CODE = {Trip.ODO: 0, Trip.A: 1, Trip.B: 2, Trip.F: 3}


class Telltale(StrEnum):
    """Every lamp the manual lists, with its manual reference."""

    #: 1. Economy and power indicator: green for economy, amber for power. The
    #: power lamp also blinks when the engine idles more than ~20 seconds.
    ECO = "eco"
    POWER = "power"
    #: 2. High beam.
    HIGH_BEAM = "high-beam"
    #: 3 and 4. Turn signals.
    TURN_LEFT = "turn-left"
    TURN_RIGHT = "turn-right"
    #: 5. Low fuel indication.
    LOW_FUEL = "low-fuel"
    #: 6. Malfunction indicator lamp.
    MIL = "mil"
    #: 7. Smart-connect notification LED: green, blinks on a call or message.
    NOTIFY = "notify"


#: The lamps sit outside the display glass. Manual page 25 numbers them 1-7 and
#: every one of them is a lamp on the surround, not a segment on the display.
BEZEL_TELLTALES = tuple(Telltale)


class SegIcon(StrEnum):
    """Icons drawn inside the display glass rather than on the surround."""

    BLUETOOTH = "bluetooth"
    PHONE_BATTERY = "phone-battery"
    SIGNAL = "signal"
    HELMET = "helmet"
    SIDE_STAND = "side-stand"
    LOW_BATTERY = "low-battery"
    SERVICE = "service"
    OIL = "oil"
    COOLANT = "coolant"
    ENVELOPE = "envelope"
    NAV_PIN = "nav-pin"


@dataclass
class NavFrame:
    """What the cluster has been told by the phone.

    The HUD is not driven by one packet. `5A 4E` sets the arrow and the
    distances, `5B 0x4F` sets the street line, and they alternate on a
    five-second boundary, so each arrival updates one part of this and the
    other parts keep their previous values.
    """

    active: bool = False
    pictogram: int = 0
    distance_m: int = 0
    eta_minutes: int = 0
    remaining_m: int = 0
    rows: int = 1
    text: str = ""
    text_seen_at: float = 0.0
    control_seen_at: float = 0.0
    #: Notes raised while decoding phone frames, newest last. Shown on the
    #: web display rather than swallowed.
    notes: list[str] = field(default_factory=list)

    def phase_text(self) -> str:
        """The street name, or the metrics, alternating as the app intends."""
        if not self.text:
            return ""
        return self.text


@dataclass
class Message:
    """One line on the alpha-numeric display, with how long it stays."""

    text: str
    until: float
    blink: bool = False
    kind: str = "info"


@dataclass
class ClusterState:
    """Everything the cluster knows, and everything it would be showing."""

    # --- identity -------------------------------------------------------
    #: Advertised name. Real clusters use `TVSBT` followed by alphanumerics,
    #: and the app's scan matches on the name because this vehicle advertises
    #: no service UUID. Kept obviously synthetic.
    name: str = "TVSBT0000001"
    #: Locally administered static address, so nothing here collides with a
    #: real device on the bench.
    address: str = "5E:00:01:01:00:01"

    # --- ignition and ride ---------------------------------------------
    ignition: bool = True
    engine_running: bool = True
    speed_kmh: float = 0.0
    odometer_tenths: int = 0
    fuel_litres: float = FUEL_HALF_L
    engine_temp_c: float = TEMP_IDLE_C
    battery_voltage: float = 12.6
    throttle_percent: float = 0.0
    average_kmpl: float = 45.0

    # --- time -----------------------------------------------------------
    #: Simulated clock, in seconds since midnight. The cluster syncs this
    #: from the phone on pairing, which is what the lap and 0-60 timers are
    #: measured against.
    now: float = 0.0

    # --- trips and stats ------------------------------------------------
    selected_trip: Trip = Trip.A
    trip_a_tenths: int = 0
    trip_b_tenths: int = 0
    trip_f_tenths: int = 0
    lap_distance_tenths: int = 0
    lap_seconds: float = 0.0
    shortest_time_seconds: float = 0.0
    average_speed_kmh: float = 0.0
    top_speed_kmh: float = 0.0
    zero_to_sixty_seconds: float = 0.0
    best_lap_seconds: float = 0.0
    best_lap_distance_tenths: int = 0
    best_average_speed_kmh: float = 0.0
    best_top_speed_kmh: float = 0.0
    best_shortest_time_seconds: float = 0.0
    previous_lap_seconds: float = 0.0
    previous_lap_distance_tenths: int = 0
    previous_average_speed_kmh: float = 0.0

    # --- indicators -----------------------------------------------------
    telltales: set[Telltale] = field(default_factory=set)
    segment_icons: set[SegIcon] = field(default_factory=set)
    mode: Mode = Mode.STREET

    # --- phone link -----------------------------------------------------
    phone_connected: bool = False
    phone_battery_bars: int = 0
    phone_signal_bars: int = 0
    phone_time: tuple[int, int, int] | None = None
    nav: NavFrame = field(default_factory=NavFrame)
    #: The name the cluster puts in '<NAME> DISCONNECTED'. The manual uses
    #: JOHN; the simulator uses the paired phone's name when one is set.
    phone_name: str = "PHONE"

    # --- call and message state ----------------------------------------
    caller: str | None = None
    missed_calls: int = 0
    sms_from: str | None = None

    # --- messages -------------------------------------------------------
    message: Message | None = None
    message_log: list[Message] = field(default_factory=list)

    # --- simulation switches -------------------------------------------
    #: Emit `5A 19`. The owner's cluster never sent it, despite it being in
    #: the frame table and carrying the economy and distance-to-empty fields.
    #: Leaving it off is the default; switching it on tests that the phone's
    #: tiles do not light up from a frame real hardware never produces.
    emit_frame_19: bool = False
    #: Emit `5A 16`, the frame with no layout table anywhere. Always on by
    #: default: it was the second most common frame in the capture.
    emit_frame_16: bool = True
    #: Free-form overrides for `5A 16`, keyed by the placeholder field names
    #: in `protocol.FRAME_16_FIELDS`.
    frame_16_fields: dict[str, int] | None = None
    #: Send `9B F2` challenges. This vehicle never sent one.
    send_challenge: bool = False
    #: When set, index 6 of `5A 10` is written verbatim. `7` reproduces the
    #: value the owner's cluster sent while its own gauge read FUEL EMPTY.
    raw_fuel_override: int | None = None
    #: Ignition-on time, for the ten-second helmet blink.
    ignition_at: float = 0.0
    side_stand: bool = False
    side_stand_fault: bool = False

    # --- internal timers ------------------------------------------------
    engine_seconds: float = 0.0
    idle_seconds: float = 0.0
    _lap_running: bool = False
    _lap_started_at: float = 0.0
    _zero_to_sixty_started_at: float | None = None
    _speed_moving_total: float = 0.0
    #: Carried fraction of a tenth of a kilometre.
    #:
    #: A 20 ms tick at 60 km/h covers 0.00033 km, which is 0.0033 tenths.
    #: Accumulating that straight into an integer counter and truncating loses
    #: all of it, and the odometer never moves at any speed. The fraction has to
    #: be carried between ticks, and the odometer is refreshed from the
    #: float rather than incremented.
    _odometer_remainder: float = 0.0
    _trip_remainder: float = 0.0
    _lap_distance_remainder: float = 0.0
    #: Speed as of the end of the previous tick, so a threshold crossing is
    #: detectable even when the speed is changed between ticks.
    _speed_last_tick: float = 0.0

    def _add_distance(self, km: float) -> None:
        """Add [km] to every counter that tracks distance travelled."""
        tenths = km * 10.0
        self._odometer_remainder += tenths
        whole = int(self._odometer_remainder)
        self._odometer_remainder -= whole
        self.odometer_tenths += whole

        if self.selected_trip in (Trip.A, Trip.B, Trip.F):
            self._trip_remainder += tenths
            trip_whole = int(self._trip_remainder)
            self._trip_remainder -= trip_whole
            if self.selected_trip == Trip.A:
                self.trip_a_tenths += trip_whole
            elif self.selected_trip == Trip.B:
                self.trip_b_tenths += trip_whole
            else:
                self.trip_f_tenths += trip_whole

        if self._lap_running:
            self._lap_distance_remainder += tenths
            lap_whole = int(self._lap_distance_remainder)
            self._lap_distance_remainder -= lap_whole
            self.lap_distance_tenths += lap_whole

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------

    def clock(self) -> _dt.datetime:
        base = _dt.datetime(2026, 1, 1)
        return base + _dt.timedelta(seconds=self.now)

    def odometer_km(self) -> float:
        return self.odometer_tenths / 10.0

    def selected_trip_tenths(self) -> int:
        return {
            Trip.ODO: self.odometer_tenths,
            Trip.A: self.trip_a_tenths,
            Trip.B: self.trip_b_tenths,
            Trip.F: self.trip_f_tenths,
        }[self.selected_trip]

    def fuel_bars(self) -> int:
        """Fuel on the six-bar scale, from the manual's litre thresholds.

        The manual gives three points and no formula: all six bars above about
        5 litres, three bars at 3.2, and the last bar blinking at the 1.2-litre
        minimum safe level. Interpolating linearly between the two known points
        reproduces all three, and a floor rather than a round is used so 3.2
        lands on three bars rather than four.
        """
        if self.fuel_litres >= FUEL_FULL_L:
            return GAUGE_BARS
        if self.fuel_litres <= FUEL_HALF_L:
            # 3.2 L is three bars and 1.2 L is one, so three bars span two
            # litres: one bar per 1.0 L below the half mark.
            bars = int(self.fuel_litres - FUEL_RESERVE_L) + 1
        else:
            # 3.2 L is three bars and 5.0 L is six: one bar per 0.6 L.
            bars = int((self.fuel_litres - FUEL_HALF_L) / 0.6) + 3
        return max(0, min(GAUGE_BARS, bars))

    def fuel_at_reserve(self) -> bool:
        """Manual: at 1.2 litres the last bar blinks and FUEL EMPTY shows."""
        return self.fuel_litres <= FUEL_RESERVE_L

    def temp_bars(self) -> int:
        if self.engine_temp_c >= TEMP_OVER_RANGE_C:
            return GAUGE_BARS
        if self.engine_temp_c <= TEMP_IDLE_C:
            return 0
        frac = (self.engine_temp_c - TEMP_IDLE_C) / (TEMP_OVER_RANGE_C - TEMP_IDLE_C)
        return max(0, min(GAUGE_BARS, int(frac * GAUGE_BARS) + 1))

    def temp_over_range(self) -> bool:
        return self.engine_temp_c >= TEMP_OVER_RANGE_C

    @property
    def side_stand_lit(self) -> bool:
        """Whether the side-stand indicator should be shown lit.

        Read from [side_stand] directly rather than from [segment_icons],
        because the icon set is only refreshed on a tick and a caller that sets
        the field and renders immediately would otherwise see the indicator
        stay off. The icon is kept in the set as well, because it is what the
        display layer and the snapshot both read.
        """
        return self.side_stand or SegIcon.SIDE_STAND in self.segment_icons

    def fuel_sensor_fault(self) -> bool:
        """`0x11` index 2 is a fault flag, **not** a fuel level.

        In the official class it is `fuelSensorFailure`. Reading it as a level
        produced the opposite error to the fuel gauge. It is only set here when
        a fault has been injected, which is what a real sensor fault would do.
        """
        return self.side_stand_fault  # reused as the generic fault switch

    def shortest_time_display(self) -> float:
        """What the 0-60 element shows, including the manual's 15 s cap."""
        if self.zero_to_sixty_seconds <= 0:
            return 0.0
        return min(self.zero_to_sixty_seconds, SHORTEST_TIME_DISPLAY_CAP_S)

    # ------------------------------------------------------------------
    # Frames the cluster sends
    # ------------------------------------------------------------------

    def speedometer_1(self) -> P.Speedometer1:
        """Build `5A 10`.

        Index 6 is the fuel field. The app takes its **low nibble** as a bar
        count, which is exactly what the vendor's own `getFuelLevel()` does by
        keeping the last hex character. Across 772 captured frames that byte
        took only two values, 2 and 7, and the dash was photographed reading
        FUEL EMPTY while it read 7 - a six-bar gauge cannot express 7, so the
        app now drops out-of-range values instead of clamping them. This
        simulator can emit either: leave `raw_fuel_override` unset for a value
        the gauge can actually show, or set it to 7 to reproduce the frame real
        hardware sent.
        """
        raw = self.raw_fuel_override
        if raw is None:
            raw = self.fuel_bars()
        return P.Speedometer1(
            speed_kmh=int(round(self.speed_kmh)),
            odometer_tenths=self.odometer_tenths,
            fuel_raw=raw,
            average_speed_kmh=int(round(self.average_speed_kmh)),
            trip_status=TRIP_STATUS_CODE[self.selected_trip],
            top_speed_kmh=int(round(self.top_speed_kmh)),
            throttle_percent=int(round(self.throttle_percent * 2)),
            backlight=0,
            bt_pair_mode=0,
            location_tag=0,
            time_to_60_tenths=int(round(self.zero_to_sixty_seconds * 10)),
            trip_f_tenths=self.trip_f_tenths,
            engine_rpm=self._rpm(),
        )

    def speedometer_2(self) -> P.Speedometer2:
        """Build `5A 11`."""
        return P.Speedometer2(
            fuel_sensor_fault=1 if self.fuel_sensor_fault() else 0,
            vehicle_state=1 if self.ignition else 0,
            service_reminder=1 if SegIcon.SERVICE in self.segment_icons else 0,
            vehicle_state_1=0,
            software_version=0x12,  # as seen on the owner's cluster
            mil_blink_code=1 if Telltale.MIL in self.telltales else 0,
            vehicle_model=0,
            vehicle_diagnostics=0,
            isg_blink_code=0,
            turn_indicator_status=self._turn_status(),
            connector_status=0,
        )

    def speedometer_3(self) -> P.Speedometer3:
        """Build `5A 19`. Off by default: this cluster never sent it.

        Range on the Ntorq 125 is not a display element at all - the manual
        lists every element the dash has and range is not among them, so this
        value exists to be decoded and ignored, which is the honest behaviour.
        """
        remaining_km = self.fuel_litres * self.average_kmpl
        return P.Speedometer3(
            iss_duration=0,
            ota_status=0,
            average_fuel_economy=int(self.average_kmpl),
            instantaneous_fuel_economy=int(self.average_kmpl),
            distance_to_empty_km=int(remaining_km),
            iss_count=0,
        )

    def speedometer_4(self) -> P.Speedometer4:
        """Build `5A 18`."""
        return P.Speedometer4(
            engine_load=int(round(self.throttle_percent * 0.6)),
            accumulated_fuel_injection_time=0,
            barometric_pressure=0,
            intake_air_temperature=int(self.engine_temp_c - 20),
            engine_temperature_c=int(round(self.engine_temp_c)),
            fuel_injection_time=0,
            battery_voltage=int(round(self.battery_voltage * 10)),
            engine_running_time=int(self.engine_seconds) & 0xFFFF,
            distance_traveled=self.odometer_tenths & 0xFFFF,
            fuel_injection_volume=0,
        )

    def identity(self) -> bytes:
        """Build `5B 42` - the cluster's own BLE address at bytes 7..12."""
        return P.encode_identity(self.address)

    def challenge(self) -> bytes:
        """Build `9B F2`. Off by default; this vehicle never sent one."""
        return P.encode_challenge(bytes(range(16)))

    def _turn_status(self) -> int:
        left = Telltale.TURN_LEFT in self.telltales
        right = Telltale.TURN_RIGHT in self.telltales
        if left and right:
            return 3
        if left:
            return 1
        if right:
            return 2
        return 0

    def _rpm(self) -> int:
        if not self.engine_running:
            return 0
        return int(1500 + self.speed_kmh * 320)

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, dt: float) -> None:
        """Advance the simulation by [dt] seconds."""
        speed_before = self._speed_last_tick
        self.now += dt

        if self.engine_running and self.ignition:
            distance_km = self.speed_kmh * dt / 3600.0
            self._add_distance(distance_km)
            self._speed_moving_total += distance_km

            # Idle burns a trickle; riding burns roughly with the throttle.
            burn_l = (0.00004 + 0.000010 * self.throttle_percent) * dt
            self.fuel_litres = max(0.0, self.fuel_litres - burn_l)

            target_c = 88.0 if self.speed_kmh > 1.0 else TEMP_IDLE_C + 18.0
            self.engine_temp_c += (target_c - self.engine_temp_c) * min(1.0, dt / 90.0)
            self.engine_seconds += dt
            self.idle_seconds = self.idle_seconds + dt if self.speed_kmh < 1.0 else 0.0
        else:
            self.engine_temp_c += (TEMP_IDLE_C - self.engine_temp_c) * min(1.0, dt / 600.0)
            self.idle_seconds = 0.0

        self._tick_laps(dt)
        self._tick_zero_to_sixty(speed_before)
        self._tick_speed_stats(dt)
        self._update_automatic_states()

        self.top_speed_kmh = max(self.top_speed_kmh, self.speed_kmh)
        # Remember where the speed was at the end of this tick. Reading
        # `speed_kmh` for the "before" value would see the speed a caller set
        # between two ticks, which means every threshold crossing is invisible
        # to the edge detectors - a 0-60 timer that never fires, a lap timer
        # that never starts.
        self._speed_last_tick = self.speed_kmh

    def _tick_laps(self, dt: float) -> None:
        """Lap distance and timer: start above 2 km/h, stop below it.

        Manual 2a, verbatim: "once the vehicle speed crosses 2 km/h, the lap
        distance counter starts counting distance automatically and stops when
        the speed goes below 2 km/h".

        The condition is written as *is it running and is it above the
        threshold* rather than as an edge detection on the previous tick. An
        edge detector misses the case where the speed is changed from outside
        between two ticks - which is exactly what a scenario or a web control
        does - and the lap timer would then never start at all. Both forms agree
        while the speed is being driven smoothly, and only this form survives a
        speed being set outright.
        """
        above = self.speed_kmh >= LAP_SPEED_THRESHOLD_KMH

        if above and not self._lap_running:
            self._lap_running = True
            self._lap_started_at = self.now
        elif not above and self._lap_running:
            self._lap_running = False
            self._record_completed_lap()

        if self._lap_running:
            self.lap_seconds = self.now - self._lap_started_at
            self._add_distance(self.speed_kmh * dt / 3600.0)

    def _record_completed_lap(self) -> None:
        """Close the lap and fold it into sport and ride-stats records."""
        if self.lap_seconds <= 0:
            return
        average = (self.lap_distance_tenths / 10.0 / self.lap_seconds * 3600.0)
        self.previous_lap_seconds = self.lap_seconds
        self.previous_lap_distance_tenths = self.lap_distance_tenths
        self.previous_average_speed_kmh = average

        if self.lap_seconds > self.best_lap_seconds:
            self.best_lap_seconds = self.lap_seconds
            self.best_lap_distance_tenths = self.lap_distance_tenths
            self.best_average_speed_kmh = average
        if self.lap_distance_tenths > self.best_lap_distance_tenths:
            self.best_top_speed_kmh = self.top_speed_kmh
        if 0 < self.shortest_time_seconds < self.best_shortest_time_seconds:
            self.best_shortest_time_seconds = self.shortest_time_seconds

        self.lap_seconds = 0.0
        self.lap_distance_tenths = 0

    def _tick_zero_to_sixty(self, speed_before: float) -> None:
        """Time to 60 km/h, and the manual's rule for when to forget it.

        The clock starts when speed crosses 2 km/h (manual 2c: "The timer starts
        once the vehicle speed crosses 2 km/h") and stops at 60. A run is only
        recorded if it took measurable time: jumping the speed from 0 to 65 in
        a single tick crosses both thresholds at once, which is not a 0-60 run,
        and recording `0.0` would put a plausible-looking figure on the dash for
        something that never happened.
        """
        # Start on rising past 2 km/h, whether that happened between two ticks
        # or gradually. See `_tick_laps` for why this is not an edge detector.
        if (
            self._zero_to_sixty_started_at is None
            and self.speed_kmh >= LAP_SPEED_THRESHOLD_KMH
        ):
            self._zero_to_sixty_started_at = self.now

        if (
            self._zero_to_sixty_started_at is not None
            and self.speed_kmh >= 60.0
            and speed_before < 60.0
        ):
            elapsed = self.now - self._zero_to_sixty_started_at
            self._zero_to_sixty_started_at = None
            if elapsed > 0:
                self.zero_to_sixty_seconds = elapsed
                if (
                    self.shortest_time_seconds == 0.0
                    or elapsed < self.shortest_time_seconds
                ):
                    self.shortest_time_seconds = elapsed
        elif self.speed_kmh >= 60.0 and self._zero_to_sixty_started_at is not None:
            # Already at or above 60 km/h and still climbing: the run finished
            # on an earlier tick, or the speed jumped straight past it. Either
            # way the timer is done.
            self._zero_to_sixty_started_at = None

        # Manual 2c: the value disappears if speed drops below 2 km/h.
        if self.speed_kmh < LAP_SPEED_THRESHOLD_KMH:
            self.zero_to_sixty_seconds = 0.0
            self._zero_to_sixty_started_at = None

    def _tick_speed_stats(self, dt: float) -> None:
        if self.engine_seconds > 0:
            self.average_speed_kmh = (
                self._speed_moving_total / self.engine_seconds * 3600.0
            )

    def _update_automatic_states(self) -> None:
        """Rules the cluster applies to itself, with no phone involved."""

        if self.ignition and self.now - self.ignition_at < BLINK_AFTER_IGNITION_S:
            self.segment_icons.add(SegIcon.HELMET)
        else:
            self.segment_icons.discard(SegIcon.HELMET)

        if self.fuel_at_reserve():
            self.telltales.add(Telltale.LOW_FUEL)
        else:
            self.telltales.discard(Telltale.LOW_FUEL)

        # Manual 1k: while the tank is at reserve the display switches itself
        # to TRIP F and counts from 0.0. Once two bars are showing again, it
        # goes back to TRIP A and TRIP F is cleared.
        if self.fuel_at_reserve():
            self.selected_trip = Trip.F
        elif self.selected_trip == Trip.F and self.fuel_bars() >= 2:
            self.trip_f_tenths = 0
            self.selected_trip = Trip.A

        if self.side_stand:
            self.segment_icons.add(SegIcon.SIDE_STAND)
        else:
            self.segment_icons.discard(SegIcon.SIDE_STAND)

        # When the stand comes down the engine stops, because "if the side stand
        # is deployed the vehicle will not start" (manual 1m). Without this the
        # cluster happily reports a running engine at 60 km/h with the stand
        # down, which is a state the real vehicle cannot be in.
        if self.side_stand:
            self.engine_running = False
            self.throttle_percent = 0.0

        # Manual: above 2 km/h the non-riding modes hand back to street mode.
        if self.speed_kmh > LAP_SPEED_THRESHOLD_KMH and self.mode in (
            Mode.RIDE_STATS_LAST,
            Mode.RIDE_STATS_BEST,
            Mode.BT_PAIR,
            Mode.SET_CLOCK,
        ):
            self.mode = Mode.STREET

        if self.idle_seconds > IDLE_POWER_BLINK_S and self.ignition:
            self.telltales.add(Telltale.POWER)
        else:
            self.telltales.discard(Telltale.POWER)

        self._update_messages()

    # ------------------------------------------------------------------
    # The alpha-numeric display
    # ------------------------------------------------------------------

    def _update_messages(self) -> None:
        """What the text area shows, highest priority first.

        The cluster's own warnings outrank anything the phone sent. The manual
        never states a priority order, but a fuel warning that a navigation
        message can cover would be a defect, and 'FUEL EMPTY' is the one
        message the manual tells the rider to act on immediately.

        The warnings are *not* subject to the expiry check that governs the rest:
        they are refreshed every tick, so a message with a fixed lifetime - the
        ten-second missed-call count, say - cannot expire while an engine is
        overheating and leave the warning off.
        """
        if self.temp_over_range():
            self._set_message("ENGINE WARNING", kind="warning", blink=True)
            return
        if self.fuel_sensor_fault():
            self._set_message("CHECK FUEL SENSOR", kind="warning", blink=True)
            return
        if self.fuel_at_reserve():
            self._set_message("FUEL EMPTY", kind="warning", blink=True)
            return

        if self.message is not None:
            if self.message.until > self.now:
                return
            # Expired. Fall through and re-derive what should be there now,
            # rather than leaving a stale line on the display.
            self.message = None

        if self.nav.active and self.nav.text:
            self._set_message(self.nav.text, kind="nav", ttl=30.0)
        elif self.caller:
            self._set_message(f"CALL FROM {self.caller}", kind="call", ttl=30.0)
        elif self.sms_from:
            self._set_message(f"SMS FROM {self.sms_from}", kind="sms", ttl=30.0)
        elif self.missed_calls:
            self._set_message(
                f"{self.missed_calls} MISSED CALLS",
                kind="call",
                ttl=MISSED_CALL_MESSAGE_S,
            )
        elif self.mode != Mode.STREET:
            self._set_message(self.mode.label, kind="mode", ttl=30.0)

    def _set_message(
        self,
        text: str,
        kind: str = "info",
        ttl: float = CONNECTED_MESSAGE_S,
        blink: bool = False,
    ) -> None:
        if self.message is not None and self.message.text == text:
            self.message.until = self.now + ttl
            self.message.blink = blink
            return
        self.message = Message(text=text, until=self.now + ttl, blink=blink, kind=kind)
        self.message_log.append(self.message)
        del self.message_log[:-200]

    def announce(self, text: str, ttl: float = CONNECTED_MESSAGE_S, kind: str = "info") -> None:
        """Raise a transient message, as the cluster does on a state change.

        Synchronous on purpose: a caller that announces a message and renders
        immediately must see that message, not the previous one.
        """
        self._set_message(text, kind=kind, ttl=ttl)

    def on_phone_connected(self, name: str | None = None) -> None:
        """Manual: 'CONNECTION SUCCESSFUL' for three seconds, plus the BT icon,
        the phone's battery icon and its signal icon."""
        if name:
            self.phone_name = name
        self.phone_connected = True
        self.segment_icons.add(SegIcon.BLUETOOTH)
        self.segment_icons.add(SegIcon.PHONE_BATTERY)
        self.segment_icons.add(SegIcon.SIGNAL)
        self.announce("CONNECTION SUCCESSFUL", ttl=CONNECTED_MESSAGE_S, kind="link")
        if self.mode == Mode.BT_PAIR:
            self.mode = Mode.STREET

    def on_phone_disconnected(self) -> None:
        """Manual: on losing the link the cluster shows '<NAME> DISCONNECTED'."""
        self.phone_connected = False
        for icon in (SegIcon.BLUETOOTH, SegIcon.PHONE_BATTERY, SegIcon.SIGNAL):
            self.segment_icons.discard(icon)
        self.nav.active = False
        self.nav.text = ""
        self.announce(f"{self.phone_name} DISCONNECTED", ttl=5.0, kind="link")

    # ------------------------------------------------------------------
    # What the phone sent
    # ------------------------------------------------------------------

    def apply_phone_frame(self, frame: P.PhoneFrame) -> None:
        """Fold one phone -> cluster frame into what the dash would show."""
        if frame.notes:
            self.nav.notes = list(frame.notes)

        if frame.kind == "nav-control":
            nav = frame.payload
            self.nav.active = bool(nav.active)
            self.nav.pictogram = nav.pictogram
            self.nav.distance_m = nav.distance_m
            self.nav.eta_minutes = nav.eta_minutes
            self.nav.remaining_m = nav.remaining_m
            self.nav.rows = nav.rows
            self.nav.control_seen_at = self.now

        elif frame.kind == "text" and isinstance(frame.payload, P.TextRow):
            row = frame.payload
            is_nav = row.data_id in (
                P.PhoneDataId.NAVIGATION_TEXT,
                P.PhoneDataId.NAVIGATION_TEXT_2,
            )
            # The cluster tells a navigation row from a caller or SMS row by
            # its data id. A message row must not be able to masquerade as a
            # street name on the HUD, so `nav.text` is only ever written by a
            # navigation id.
            if is_nav:
                self.nav.text = row.text
                self.nav.text_seen_at = self.now
            else:
                self._set_message(row.text, kind="phone", ttl=8.0)

        elif frame.kind == "mobile-data" and isinstance(frame.payload, P.MobileDataPacket):
            mobile = frame.payload
            self.phone_signal_bars = mobile.signal_bars
            self.phone_battery_bars = mobile.battery_bars
            if mobile.time_hms:
                self.phone_time = mobile.time_hms
            if mobile.find_my_scooter:
                self.nav.notes.append(
                    "find-my-scooter flag set; only the enriched layout carries "
                    "one, at byte 17 where the official packet writes zero"
                )

        elif frame.kind == "handshake-response":
            self.nav.notes.append(
                "handshake answered; this cluster never sent a challenge"
            )

        elif frame.kind == "unknown":
            self.nav.notes.append(
                f"unrecognised frame {frame.raw[0]:02X} {frame.data_id:02X} from "
                "the phone; the cluster would ignore it"
            )

        # The display is recomputed here rather than waiting for the next tick.
        # A cluster that showed the previous text for up to 20 ms after the
        # phone sent a new one would be wrong, and a caller inspecting the state
        # straight after a write would see the old message.
        self._update_messages()

    # ------------------------------------------------------------------
    # Fault injection
    # ------------------------------------------------------------------

    #: Named faults the operator can apply from the web display or the CLI.
    FAULTS = (
        "low-fuel",
        "reserve-fuel",
        "empty-tank",
        "full-tank",
        "overheating",
        "fuel-sensor",
        "mil",
        "side-stand",
        "service-due",
        "battery-low",
        "raw-fuel-7",
        "raw-fuel-2",
        "zero-odometer",
    )

    def inject(self, name: str) -> None:
        """Put the cluster into a named fault state.

        The derived display state is recomputed before returning. A fault that
        only became visible on the next tick would mean the web control and the
        CLI both appeared to do nothing, and a caller reading the state straight
        after would see the pre-fault cluster.
        """
        self._inject(name)
        self._update_automatic_states()

    def _inject(self, name: str) -> None:
        if name in ("low-fuel", "reserve-fuel"):
            self.fuel_litres = FUEL_RESERVE_L
        elif name == "empty-tank":
            self.fuel_litres = 0.4
        elif name == "full-tank":
            self.fuel_litres = FUEL_FULL_L
        elif name == "overheating":
            self.engine_temp_c = TEMP_OVER_RANGE_C + 5
        elif name == "fuel-sensor":
            self.side_stand_fault = True
            self.fuel_litres = FUEL_FULL_L
        elif name == "mil":
            self.telltales.add(Telltale.MIL)
        elif name == "side-stand":
            self.side_stand = True
        elif name == "service-due":
            self.segment_icons.add(SegIcon.SERVICE)
        elif name == "battery-low":
            self.segment_icons.add(SegIcon.LOW_BATTERY)
        elif name == "raw-fuel-7":
            # The value the owner's cluster sent while its gauge said EMPTY.
            self.raw_fuel_override = 7
        elif name == "raw-fuel-2":
            self.raw_fuel_override = 2
        elif name == "zero-odometer":
            self.odometer_tenths = 0
            self.trip_a_tenths = 0
            self.trip_b_tenths = 0
            self.trip_f_tenths = 0
        else:
            raise KeyError(f"unknown fault: {name}")

    def clear_faults(self) -> None:
        self.telltales.clear()
        self.segment_icons.clear()
        self.raw_fuel_override = None
        self.fuel_litres = FUEL_HALF_L
        self.engine_temp_c = TEMP_IDLE_C
        self.side_stand = False
        self.side_stand_fault = False
