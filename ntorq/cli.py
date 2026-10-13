# The command line.
#
# Four things, deliberately separable:
#
#   serve     the web display, optionally with the GATT server too
#   ble       the GATT server on its own, for use with `bluetoothctl` or an app
#   frames    print the frame stream as hex, for reading against a capture
#   render    write the dash to an SVG file
#
# The split matters because the GATT server needs a machine with a peripheral-
# capable adapter, and the web display does not. Someone reading the frame log
# on a desktop that cannot act as a peripheral should not have to care.

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from . import display, protocol as P
from .cluster import ClusterState, FUEL_HALF_L
from .sim import (
    SimulatedCluster,
    TICK_INTERVAL,
    iter_frames,
    replay,
)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--odometer",
        type=float,
        default=16593.0,
        metavar="KM",
        help="initial odometer in km (default: 16593, the rider's own cluster)",
    )
    parser.add_argument(
        "--fuel",
        type=float,
        default=FUEL_HALF_L,
        metavar="L",
        help="initial fuel in litres (default: 3.2, the manual's half tank)",
    )
    parser.add_argument(
        "--name",
        default="TVSBT0000001",
        help="advertised Bluetooth name (default: TVSBT0000001)",
    )
    parser.add_argument(
        "--no-startup-frames",
        action="store_true",
        help="do not emit the all-zero frames a real cluster sends at connect time",
    )


def build_cluster(args: argparse.Namespace) -> SimulatedCluster:
    state = ClusterState(
        name=args.name,
        odometer_tenths=int(round(args.odometer * 10)),
        fuel_litres=args.fuel,
        now=12 * 3600 + 22 * 60,  # the clock in the rider's photograph
    )
    return SimulatedCluster(
        state=state, emit_startup_zero_frames=not args.no_startup_frames
    )


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    from .web.app import ConsoleServer

    cluster = build_cluster(args)
    server = ConsoleServer(cluster, host=args.host, port=args.port)

    gatt = None
    if args.ble:
        gatt = _start_gatt(cluster, server.app)
        if gatt is None and not args.keep_going:
            return 2

    server.start_background()
    print(f"console: {server.url}")
    print(f"cluster: {cluster.state.name} at {cluster.state.address}")
    print(f"frames:  one per {TICK_INTERVAL * 1000:.0f} ms tick, 20 bytes each")
    print("press Ctrl-C to stop")

    try:
        while True:
            for raw in cluster.poll():
                cluster.record_out(raw)
                if gatt is not None and cluster.connected:
                    gatt.notify(raw)
            time.sleep(TICK_INTERVAL)
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.stop()
        if gatt is not None:
            gatt.stop()
    return 0


def _start_gatt(cluster: SimulatedCluster, app) -> object | None:
    """Bring up the GATT server, or explain why it could not be brought up."""
    from .gatt.server import GattError, GattServer, probe_environment

    report = probe_environment()
    print(f"ble: {report.describe()}")
    if not report.usable:
        return None
    try:
        gatt = GattServer(cluster)
        gatt.register()
        gatt.start_background()
    except (GattError, Exception) as exc:  # noqa: BLE001 - report anything
        print(f"ble: could not start: {exc}", file=sys.stderr)
        return None
    app.gatt_status = "ready, waiting for a phone"
    print(
        "ble: advertising is separate - run `ntorq-console ble --advertise` "
        "or `bluetoothctl` on a second machine"
    )
    return gatt


# ---------------------------------------------------------------------------
# ble
# ---------------------------------------------------------------------------


def cmd_ble(args: argparse.Namespace) -> int:
    from .gatt.server import GattError, GattServer, probe_environment

    cluster = build_cluster(args)
    report = probe_environment()
    print(report.describe())

    if args.check_only:
        return 0 if report.usable else 1
    if not report.usable:
        return 2

    try:
        gatt = GattServer(cluster)
        gatt.register()
    except GattError as exc:
        print(f"ble: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - BlueZ surfaces many types
        print(f"ble: registration failed: {exc}", file=sys.stderr)
        return 2

    print(f"ble: GATT service {gatt.service_uuid} registered")
    print(f"ble: write {gatt.write_uuid}, notify {gatt.read_uuid}")
    print("ble: running. Ctrl-C to stop.")

    if args.advertise:
        _advertise_blocking(cluster.state.name, args.advertise_uuid)

    gatt.start_background()
    try:
        while True:
            for raw in cluster.poll():
                cluster.record_out(raw)
                if cluster.connected:
                    gatt.notify(raw)
            if args.print:
                print(f"[{cluster.clock:8.2f}] {P.hexdump(raw[-1])}"
                      if raw else "", end="", flush=True)
            time.sleep(TICK_INTERVAL)
    except KeyboardInterrupt:
        print("\nble: stopping")
    finally:
        gatt.stop()
    return 0


def _advertise_blocking(name: str, include_uuid: bool) -> None:
    import asyncio

    from .gatt.advertise import advertise

    async def main() -> None:
        await advertise(name, include_service_uuid=include_uuid)

    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"ble: advertising: {exc}", file=sys.stderr)
        print(
            "ble: the GATT server is still running; advertise from another "
            "terminal with `bluetoothctl` if this machine cannot advertise",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


def cmd_frames(args: argparse.Namespace) -> int:
    """Print the frame stream, optionally fed a phone's writes."""
    cluster = build_cluster(args)

    if args.seconds:
        produced = list(iter_frames(cluster, args.seconds))
        for raw in produced:
            cluster.record_out(raw)
    else:
        # Live, and as fast as the tick allows.
        deadline = time.monotonic() + args.seconds if args.seconds else None
        try:
            while deadline is None or time.monotonic() < deadline:
                for raw in cluster.poll():
                    cluster.record_out(raw)
                    if args.hex:
                        print(f"{cluster.clock:9.3f}  {P.hexdump(raw)}", flush=True)
                time.sleep(TICK_INTERVAL if args.realtime else 0)
        except KeyboardInterrupt:
            pass

    if args.replay:
        sys.stdout.write(replay(cluster.recent(args.replay)) + "\n")
    elif not args.hex:
        # Summarise: a histogram of what the cluster sent, in the shape of the
        # analysis in TVSRideCompanion/AGENTS.md.
        print(_summarise(cluster))
    return 0


def _summarise(cluster: SimulatedCluster) -> str:
    import collections

    histogram: collections.Counter = collections.Counter()
    for record in cluster.log:
        if record.direction.value != "out" or len(record.raw) < 2:
            continue
        histogram[(f"{record.raw[0]:02X} {record.raw[1]:02X}", len(record.raw))] += 1
    lines = [
        f"{len(cluster.log)} frames in {cluster.clock:.1f}s of simulated time",
        "",
        "start id          len   count",
    ]
    for (start_id, length), count in sorted(histogram.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {start_id:<10} {length:>5}  {count:>6}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def cmd_render(args: argparse.Namespace) -> int:
    cluster = build_cluster(args)
    if args.scenario:
        from .scenarios import SCENARIOS

        scenario = SCENARIOS.get(args.scenario)
        if scenario is None:
            print(
                f"no scenario {args.scenario!r}; one of "
                + ", ".join(sorted(SCENARIOS)),
                file=sys.stderr,
            )
            return 2
        scenario(cluster.state)

    for _ in range(int(args.ticks)):
        cluster.tick()

    svg = display.render_svg(
        cluster.state,
        display.RenderOptions(
            all_segments=args.all_segments,
            show_debug_overlay=args.overlay,
            segment_test=args.all_segments,
        ),
    )
    if args.output == "-":
        sys.stdout.write(svg)
    else:
        Path(args.output).write_text(svg, encoding="utf-8")
        print(f"{args.output}  ({len(svg)} bytes)")
    return 0


# ---------------------------------------------------------------------------
# decode
# ---------------------------------------------------------------------------


def cmd_decode(args: argparse.Namespace) -> int:
    """Decode phone frames read from a file or stdin.

    This is the half of the tool that works with no hardware and no Bluetooth at
    all: hand it the hex from the app's Log tab and it will tell you what the
    cluster would make of each frame.
    """
    text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text()
    frames = _extract_frames(text)
    if not frames:
        print(
            "no frames found.\n"
            "Expected space-separated hex as the app's Log tab writes it:\n"
            "  5B 4A 04 02 00 00 00 02 00 00 00 04 00 00 00 00 00 00 00 FF\n"
            "or the same bytes run together:\n"
            "  5B4A0402000000020000000400000000000000FF",
            file=sys.stderr,
        )
        return 1

    for line in frames:
        print(line)
        try:
            raw = _to_bytes(line)
        except ValueError as exc:
            # A truncated paste from a terminal is the likely real case. Report
            # it against that line and keep going: a log with one bad line should
            # still yield every other frame it contains.
            print(f"  cannot decode: {exc}")
            continue
        frame = P.decode_phone_frame(raw)
        if frame is None:
            print("  too short to be a frame")
            continue
        print(f"  kind: {frame.kind}   data id: 0x{frame.data_id:02X}")
        print(f"  what: {_describe(frame)}")
        for note in frame.notes:
            print(f"  note: {note}")
    return 0


#: The shortest thing that can be called a frame: a start byte, a data id, and
#: two more bytes. Real frames are twenty.
_MIN_FRAME_BYTES = 4

#: Start bytes a phone-written frame may lead with. Read out of `StartId`
#: rather than restated, so adding one to the protocol does not require
#: remembering this list.
#:
#: Anchoring on these is what keeps the run-of-hex pattern from matching an
#: unrelated 16-character hex value - a timestamp, a MAC address, a checksum
#: printed on its own line. `00000000DEADBEEF` is all hex digits and long
#: enough, and without this it decodes into a frame nobody sent.
#:
#: The alternatives are full bytes, not a character class. Written as
#: `[5A5B9A9B]` the class would hold `{5, A, B, 9}` and every byte beginning
#: with a 9 would pass - which is how `9C4A…` came to be read as a frame.
_FRAME_LEADS = tuple(
    f"{int(member):02X}" for member in P.StartId
)
_LEAD_ALTERNATION = "|".join(_FRAME_LEADS)

#: Both spellings: the app's Log tab separates bytes with spaces, and a dump
#: pasted from a terminal runs them together. The two patterns do not overlap -
#: a run of hex digits has no spaces inside it - which is what lets each be
#: anchored properly.
_SPACED_FRAME = re.compile(
    rf"\b(?:{_LEAD_ALTERNATION})\s+"
    rf"(?:[0-9A-Fa-f]{{2}}\s+){{{_MIN_FRAME_BYTES - 2},}}"
    rf"[0-9A-Fa-f]{{2}}"
)
_RUN_FRAME = re.compile(
    rf"\b(?:{_LEAD_ALTERNATION})[0-9A-Fa-f]{{{_MIN_FRAME_BYTES * 2 - 2},}}"
    rf"(?![0-9A-Fa-f])"
)


def _extract_frames(text: str) -> list[str]:
    """Pull frame-sized byte sequences out of arbitrary text.

    Both the spaced and the run-together forms are accepted, and the two
    patterns do not overlap because a run of hex digits has no spaces inside it.
    A single pattern accepting both would match the spaced form and stop at the
    first two bytes, which produced a silent "no frames found" on input that
    was perfectly good - the exact failure this was written to avoid.
    """
    found: list[tuple[int, str]] = []
    for match in _SPACED_FRAME.finditer(text):
        found.append((match.start(), match.group(0)))
    for match in _RUN_FRAME.finditer(text):
        found.append((match.start(), match.group(0)))
    found.sort()
    return [value for _, value in found]


def _to_bytes(frame_text: str) -> bytes:
    """Turn a matched frame into bytes.

    A matched frame is either space-separated pairs (`5B 4A 04 …`) or one run of
    hex digits (`5B4A04…`). Splitting on whitespace handles the first; the second
    has to be chunked in pairs, and feeding the whole 40-character token to
    `int(…, 16)` produces a number too large for a byte rather than twenty
    bytes.
    """
    tokens = frame_text.split()
    if len(tokens) == 1:
        digits = tokens[0]
        if len(digits) % 2:
            raise ValueError(
                f"{digits} is not a whole number of bytes"
            )
        tokens = [digits[i : i + 2] for i in range(0, len(digits), 2)]
    return bytes(int(token, 16) for token in tokens)


def _describe(frame: P.PhoneFrame) -> str:
    if frame.kind == "nav-control" and hasattr(frame.payload, "little"):
        nav = frame.payload
        return (
            f"pictogram {nav.pictogram}, {nav.little.distance_m} m or "
            f"{nav.big.distance_m} m, {nav.little.eta_minutes} or "
            f"{nav.big.eta_minutes} min, {nav.rows} row(s), "
            f"{'navigating' if nav.active else 'not navigating'}"
        )
    if frame.kind == "text" and isinstance(frame.payload, P.TextRow):
        return f"text {frame.payload.text!r} ({len(frame.payload.text)} chars)"
    if frame.kind == "mobile-data" and isinstance(frame.payload, P.MobileDataPacket):
        mobile = frame.payload
        bits = [f"signal {mobile.signal_bars}", f"battery {mobile.battery_bars}"]
        if mobile.time_hms:
            bits.append(f"time {mobile.time_hms}")
        if mobile.temperature_c is not None:
            bits.append(f"temp {mobile.temperature_c}C")
        if mobile.find_my_scooter:
            bits.append("find-my flag")
        return ", ".join(bits)
    if frame.kind == "handshake-response":
        return "AES-CTR handshake response"
    return frame.kind


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ntorq-console",
        description=(
            "A digital clone of the TVS Ntorq instrument cluster. Speaks the "
            "cluster's Bluetooth LE protocol so TVSRideCompanion can be "
            "developed without a scooter."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="the web display")
    _add_common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--ble", action="store_true", help="also start the GATT server")
    serve.add_argument(
        "--keep-going",
        action="store_true",
        help="serve the display even if the GATT server could not start",
    )
    serve.set_defaults(func=cmd_serve)

    ble = sub.add_parser("ble", help="the BLE peripheral on its own")
    _add_common(ble)
    ble.add_argument(
        "--advertise",
        action="store_true",
        help="also advertise the cluster's name (needs bleak)",
    )
    ble.add_argument(
        "--advertise-uuid",
        action="store_true",
        help="include the service UUID in the advertisement. NOT what the real "
        "cluster does - it advertises no service UUID at all",
    )
    ble.add_argument("--check-only", action="store_true", help="report and exit")
    ble.add_argument("--print", action="store_true", help="print each frame")
    ble.set_defaults(func=cmd_ble)

    frames = sub.add_parser("frames", help="print the frame stream")
    _add_common(frames)
    frames.add_argument("--seconds", type=float, default=120.0)
    frames.add_argument("--hex", action="store_true", help="one line per frame")
    frames.add_argument("--replay", type=int, default=0, metavar="N",
                        help="print the last N frames and exit")
    frames.set_defaults(func=cmd_frames)

    render = sub.add_parser("render", help="write the dash to an SVG file")
    _add_common(render)
    render.add_argument("-o", "--output", default="-", help="'-' for stdout")
    render.add_argument("--scenario", default=None)
    render.add_argument("--ticks", type=int, default=0,
                        help="advance the simulation by this many ticks first")
    render.add_argument("--all-segments", action="store_true")
    render.add_argument("--overlay", action="store_true")
    render.set_defaults(func=cmd_render)

    decode = sub.add_parser(
        "decode", help="decode phone frames from hex (the app's Log tab format)"
    )
    decode.add_argument("input", nargs="?", default="-", help="file, or - for stdin")
    decode.set_defaults(func=cmd_decode)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
