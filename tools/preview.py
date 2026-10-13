#!/usr/bin/env python3
"""Render the cluster in a set of states, for eyeballing a change.

Development aid rather than part of the tool: it writes one SVG per state so a
layout change can be checked against the manual's illustration without
starting the server or connecting a phone.

    tools/preview.py /tmp/preview

Needs `rsvg-convert` or ImageMagick to rasterise; without either it still
writes the SVGs, which is the part that matters for review.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ntorq import display, protocol  # noqa: E402
from ntorq.cluster import (  # noqa: E402
    ClusterState,
    Mode,
    SegIcon,
    Telltale,
    Trip,
)


def _ride() -> ClusterState:
    state = ClusterState()
    state.odometer_tenths = 165_930
    state.trip_a_tenths = 3_526
    state.now = 12 * 3600 + 22 * 60
    state.speed_kmh = 62
    state.top_speed_kmh = 88
    state.average_speed_kmh = 47
    state.engine_temp_c = 88
    state.fuel_litres = 3.2
    state.segment_icons |= {SegIcon.BLUETOOTH, SegIcon.PHONE_BATTERY, SegIcon.SIGNAL}
    state.phone_battery_bars = 4
    state.phone_signal_bars = 3
    return state


def _navigation() -> ClusterState:
    state = _ride()
    control = protocol.NavControl(
        distance_m=550,
        eta_minutes=18,
        remaining_m=12_500,
        pictogram=0,
        rows=1,
        active=True,
    )
    state.apply_phone_frame(
        protocol.PhoneFrame("nav-control", b"", 0x4E, control, [])
    )
    state.apply_phone_frame(
        protocol.PhoneFrame(
            "text",
            b"",
            protocol.PhoneDataId.NAVIGATION_TEXT,
            protocol.TextRow("Onto Example Road", protocol.PhoneDataId.NAVIGATION_TEXT),
            [],
        )
    )
    return state


def _fuel_empty() -> ClusterState:
    state = _ride()
    state.fuel_litres = 0.9
    state.selected_trip = Trip.F
    state.trip_f_tenths = 0
    state.telltales.add(Telltale.LOW_FUEL)
    state.announce("FUEL EMPTY")
    return state


def _self_check() -> ClusterState:
    """Every segment lit, which is the manual's display-test illustration."""
    state = _ride()
    state.fuel_litres = 0.0
    state.engine_temp_c = 0.0
    state.selected_trip = Trip.ODO
    state.telltales.update({Telltale.MIL, Telltale.TURN_LEFT, Telltale.HIGH_BEAM})
    state.segment_icons.update(
        {SegIcon.HELMET, SegIcon.SIDE_STAND, SegIcon.LOW_BATTERY, SegIcon.SERVICE}
    )
    state.mode = Mode.SPORT
    return state


STATES = {
    "street": _ride,
    "navigation": _navigation,
    "fuel-empty": _fuel_empty,
    "self-check": _self_check,
}


def main(argv: list[str]) -> int:
    out_dir = pathlib.Path(argv[1] if len(argv) > 1 else "/tmp/ntorq-preview")
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for name, build in STATES.items():
        state = build()
        overlay = name == "navigation"
        svg = display.render_svg(
            state, display.RenderOptions(show_debug_overlay=overlay)
        )
        path = out_dir / f"{name}.svg"
        path.write_text(svg, encoding="utf-8")
        written.append(path)
        print(f"{path}  ({len(svg)} bytes)")

    if shutil_which("rsvg-convert"):
        for path in written:
            png = path.with_suffix(".png")
            subprocess.run(
                ["rsvg-convert", "-w", "1200", str(path), "-o", str(png)], check=True
            )
            print(f"{png}")
    return 0


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
