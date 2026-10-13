# The running cluster: a clock, a frame stream, and a log.
#
# The simulator is three things that need to happen at once, and keeping them
# apart is what makes it testable:
#
#   * a **state machine** advancing in fixed ticks, so a test can step it
#     without sleeping;
#   * a **frame source** that decides which frame is due, so a test can ask
#     for "the next frame" and get a real one;
#   * a **log** of everything in and out, because the phone has one too and
#     the two are read side by side.
#
# Frame cadence is taken from the capture rather than invented. In the 2026-10-13
# session the cluster sent 501 frames in about two minutes and the app's
# keepalive went out every 2.0 s exactly, so those are the numbers here.

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from collections.abc import Callable, Iterator

from . import protocol as P
from .cluster import ClusterState

#: Ticks per second. 20 ms is the baseline the protocol's silence-gap detection
#: is measured against (docs/PROTOCOL.md section 7), so the simulator runs at
#: the same rate the cluster's own stream does.
TICK_HZ = 50
TICK_INTERVAL = 1.0 / TICK_HZ

#: Frame periods, in seconds, from the hardware capture.
#:
#: 501 inbound frames over ~120 s is about 4 Hz for the whole set. Broken down
#: by the observed counts (132 / 75 / 136 / 143 over that window) that is
#: roughly 1.1, 0.6, 1.1 and 1.2 Hz - and the relative proportions are what
#: matter, so the periods here keep the same ordering.
PERIOD_FRAME_10 = 0.90  # speedometer 1, the commonest real frame
PERIOD_FRAME_11 = 1.70  # speedometer 2, vehicle state
PERIOD_FRAME_16 = 0.88  # the frame with no layout table
PERIOD_FRAME_18 = 0.83  # speedometer 4, engine diagnostics
PERIOD_FRAME_19 = 5.00  # speedometer 3; off by default, this cluster never sent it
PERIOD_IDENTITY = 20.0  # 5B 42 arrived six times in two minutes
PERIOD_KEEPALIVE = 2.0  # what the app sent, and what the cluster expects back

#: How long a frame log entry is kept. The phone keeps 4000 in a ring, and this
#: matches it so the two displays can be compared line for line.
LOG_CAPACITY = 4000


class Direction(StrEnum):
    #: Cluster -> phone.
    OUTBOUND = "out"
    #: Phone -> cluster.
    INBOUND = "in"


@dataclass
class FrameRecord:
    """One frame, either way, with its timestamp."""

    at: float
    direction: Direction
    raw: bytes
    data_id: int
    note: str = ""

    @property
    def hex(self) -> str:
        return P.hexdump(self.raw)


@dataclass
class LogEntry:
    """A line in the simulator's log."""

    at: float
    text: str


@dataclass
class SimulatedCluster:
    """A cluster that runs.

    Not a thread and not a loop by itself: [tick] advances it by one interval
    and [due_frames] returns whatever is ready. That keeps every behaviour
    reachable from a test without sleeping, and the CLI and the GATT server
    drive it from their own loops.
    """

    state: ClusterState = field(default_factory=ClusterState)
    #: Wall-clock seconds since start. Drives the frame schedule, so it is
    #: simulated time rather than the state's own `now`.
    clock: float = 0.0
    #: Whether the phone is connected. A real cluster only notifies once a
    #: link is up, and the app's scanner depends on that.
    connected: bool = False
    #: Send the all-zero frames a real cluster emits at connect time, before
    #: real telemetry starts. Observed in the capture, and worth reproducing:
    #: a decoder that treats them as failures is a decoder with a bug.
    emit_startup_zero_frames: bool = True

    log: deque[FrameRecord] = field(
        default_factory=lambda: deque(maxlen=LOG_CAPACITY)
    )
    messages: deque[LogEntry] = field(
        default_factory=lambda: deque(maxlen=LOG_CAPACITY)
    )

    _scheduled: dict[str, float] = field(default_factory=dict)
    _startup_frames_left: int = 0
    #: Set once a phone frame has been answered, so the identity frame can be
    #: triggered by the keepalive like a real cluster's would be.
    _identity_sent: bool = False

    def __post_init__(self) -> None:
        # Stagger the periods so the frame types do not all come due on the
        # same tick and produce a burst that never happens on real hardware.
        self._scheduled = {
            "10": self.clock,
            "16": self.clock + 0.31,
            "11": self.clock + 0.62,
            "18": self.clock + 0.93,
            "identity": self.clock + 1.24,
        }
        if self.state.send_challenge:
            self._scheduled["challenge"] = self.clock + 0.5
        self._startup_frames_left = 4 if self.emit_startup_zero_frames else 0

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Advance one tick. No sleeping, no I/O."""
        self.clock += TICK_INTERVAL
        self.state.tick(TICK_INTERVAL)

    def run_for(self, seconds: float) -> None:
        """Advance [seconds] of simulated time, in ticks."""
        steps = int(seconds / TICK_INTERVAL)
        for _ in range(steps):
            self.tick()

    # ------------------------------------------------------------------
    # Cluster -> phone
    # ------------------------------------------------------------------

    def due_frames(self) -> list[bytes]:
        """Every frame whose period has elapsed, in a sensible order.

        Called once per tick by the transports. The order is speedometer first,
        because that is the frame the app's dashboard waits on.
        """
        if self._startup_frames_left > 0:
            self._startup_frames_left -= 1
            return [bytes(P.FRAME_SIZE)]

        frames: list[bytes] = []
        now = self.clock

        def due(name: str, period: float) -> bool:
            if now - self._scheduled.get(name, now) >= period:
                self._scheduled[name] = now
                return True
            return False

        if due("10", PERIOD_FRAME_10):
            frames.append(P.encode_speedometer1(self.state.speedometer_1()))
        if due("16", PERIOD_FRAME_16) and self.state.emit_frame_16:
            frames.append(P.encode_frame_16(self.state.frame_16_fields))
        if due("11", PERIOD_FRAME_11):
            frames.append(P.encode_speedometer2(self.state.speedometer_2()))
        if due("18", PERIOD_FRAME_18):
            frames.append(P.encode_speedometer4(self.state.speedometer_4()))
        if self.state.emit_frame_19 and due("19", PERIOD_FRAME_19):
            frames.append(P.encode_speedometer3(self.state.speedometer_3()))
        if due("identity", PERIOD_IDENTITY) and not self._identity_sent:
            self._identity_sent = True
            frames.append(self.state.identity())
        if "challenge" in self._scheduled and due("challenge", 30.0):
            frames.append(self.state.challenge())

        return frames

    def poll(self) -> list[bytes]:
        """Advance one tick and return the frames it produced."""
        self.tick()
        return self.due_frames()

    def record_out(self, raw: bytes, note: str = "") -> None:
        """Note a frame the cluster sent."""
        if raw is None or len(raw) < 2:
            return
        self.log.append(
            FrameRecord(
                at=self.clock,
                direction=Direction.OUTBOUND,
                raw=bytes(raw),
                data_id=raw[1],
                note=note,
            )
        )

    # ------------------------------------------------------------------
    # Phone -> cluster
    # ------------------------------------------------------------------

    def on_phone_frame(self, raw: bytes) -> P.PhoneFrame:
        """Handle one frame written by the phone.

        Never raises on malformed input: a cluster that crashed on a bad write
        would be useless for the thing this is for, which is finding out how
        the phone behaves when it is not sure what it is talking to.
        """
        raw = bytes(raw or b"")
        frame = P.decode_phone_frame(raw)
        if frame is None:
            self.messages.append(
                LogEntry(
                    self.clock,
                    f"phone sent {len(raw)} byte(s) - too short to be a frame",
                )
            )
            return P.PhoneFrame("unknown", raw, -1, None, ["frame too short to parse"])

        self.log.append(
            FrameRecord(
                at=self.clock,
                direction=Direction.INBOUND,
                raw=raw,
                data_id=frame.data_id,
                note=frame.kind,
            )
        )
        for note in frame.notes:
            self.messages.append(LogEntry(self.clock, f"phone: {note}"))

        self.state.apply_phone_frame(frame)

        if frame.kind == "mobile-data" and not self._identity_sent:
            # A real cluster answers the first keepalive with its identity.
            self._identity_sent = False
            self._scheduled["identity"] = self.clock

        return frame

    # ------------------------------------------------------------------
    # Link
    # ------------------------------------------------------------------

    def connect(self, name: str | None = None) -> None:
        """The phone connected. Cluster -> 'CONNECTION SUCCESSFUL'."""
        self.connected = True
        self.state.on_phone_connected(name)
        self.messages.append(
            LogEntry(self.clock, f"phone connected: {self.state.phone_name}")
        )

    def disconnect(self) -> None:
        self.connected = False
        self.state.on_phone_disconnected()
        self.messages.append(LogEntry(self.clock, "phone disconnected"))

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def recent(self, count: int = 40) -> list[FrameRecord]:
        """The last [count] frames, newest last."""
        if count >= len(self.log):
            return list(self.log)
        return list(self.log)[-count:]

    def snapshot(self) -> dict:
        """Everything the web display needs, as plain data."""
        state = self.state
        return {
            "clock": self.clock,
            "connected": self.connected,
            "name": state.name,
            "address": state.address,
            "mode": state.mode.value,
            "modeLabel": state.mode.label,
            "trip": state.selected_trip.value,
            "odometerKm": round(state.odometer_km(), 1),
            "speedKmh": round(state.speed_kmh, 1),
            "topSpeedKmh": round(state.top_speed_kmh, 1),
            "averageSpeedKmh": round(state.average_speed_kmh, 1),
            "fuelLitres": round(state.fuel_litres, 2),
            "fuelBars": state.fuel_bars(),
            "tempC": round(state.engine_temp_c, 1),
            "tempBars": state.temp_bars(),
            "batteryV": round(state.battery_voltage, 1),
            "ignition": state.ignition,
            "engineRunning": state.engine_running,
            "sideStand": state.side_stand_lit,
            "telltales": sorted(t.value for t in state.telltales),
            "segmentIcons": sorted(s.value for s in state.segment_icons),
            "message": state.message.text if state.message else None,
            "messageKind": state.message.kind if state.message else None,
            "nav": {
                "active": state.nav.active,
                "pictogram": state.nav.pictogram,
                "distanceM": state.nav.distance_m,
                "etaMinutes": state.nav.eta_minutes,
                "remainingM": state.nav.remaining_m,
                "text": state.nav.text,
                "rows": state.nav.rows,
                "notes": list(state.nav.notes),
            },
            "switches": {
                "emitFrame19": state.emit_frame_19,
                "emitFrame16": state.emit_frame_16,
                "sendChallenge": state.send_challenge,
                "rawFuelOverride": state.raw_fuel_override,
                "startupZeroFrames": self.emit_startup_zero_frames,
            },
            "frames": [
                {
                    "at": round(r.at, 2),
                    "dir": r.direction.value,
                    "hex": r.hex,
                    "dataId": r.data_id,
                    "note": r.note,
                }
                for r in self.recent(60)
            ],
            "messages": [
                {"at": round(m.at, 2), "text": m.text} for m in list(self.messages)[-20:]
            ],
        }


class Clock:
    """A monotonic wall-clock helper, so a caller does not repeat itself."""

    def __init__(self) -> None:
        self._origin = time.monotonic()

    def now(self) -> float:
        return time.monotonic() - self._origin

    def sleep_until(self, next_tick: float) -> None:
        """Sleep until [next_tick] seconds have elapsed."""
        remaining = next_tick - self.now()
        if remaining > 0:
            time.sleep(remaining)


def run_forever(
    cluster: SimulatedCluster,
    send: Callable[[bytes], None],
    *,
    realtime: bool = True,
) -> None:
    """Drive [cluster] and hand every frame to [send].

    [realtime] paces the loop against the wall clock. Turning it off makes the
    loop run as fast as it can, which is what the frame-log test uses and what
    the CLI's `--fast` does.
    """
    clock = Clock()
    next_tick = clock.now() + TICK_INTERVAL
    while True:
        frames = cluster.poll()
        for raw in frames:
            cluster.record_out(raw)
            if cluster.connected:
                send(raw)
        if realtime:
            clock.sleep_until(next_tick)
            next_tick += TICK_INTERVAL


def iter_frames(
    cluster: SimulatedCluster, seconds: float
) -> Iterator[bytes]:
    """Yield every frame the cluster produces over [seconds].

    Deterministic and sleep-free, so a test can assert on the exact stream.
    """
    steps = int(seconds / TICK_INTERVAL)
    for _ in range(steps):
        cluster.tick()
        yield from cluster.due_frames()


def replay(records: list[FrameRecord]) -> str:
    """Format a log the way the phone's Log tab does, for side-by-side reading."""
    return "\n".join(
        f"{r.at:9.3f}  {r.direction.value:>3}  {r.hex}"
        + (f"   {r.note}" if r.note else "")
        for r in records
    )
