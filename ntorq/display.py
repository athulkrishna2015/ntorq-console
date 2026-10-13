# The cluster face, as SVG.
#
# Laid out from two sources: TVS's own Ntorq 125 owner manual, whose
# all-segments-on illustration shows every element with its position, and a
# photograph of the owner's own cluster in its normal running state. The
# manual governs; the photograph confirms the palette and the proportions.
#
# Nothing here is traced from either. Every glyph is drawn from primitives, so
# there is no third-party artwork in the repository and nothing derived from
# the rider's own machine beyond measurements.
#
# The renderer emits a standalone SVG string, which is what the web display
# serves and what `ntorq-console render` writes to a file. Keeping the SVG on
# the Python side rather than in the browser means the same drawing is used
# offline, in a test, and on screen - there is one implementation of the
# cluster face, not two that can disagree.

from __future__ import annotations

import html
from dataclasses import dataclass

from . import pictograms
from .cluster import (
    GAUGE_BARS,
    TEXT_ROW_CAPACITIES,
    TEXT_ROWS,
    ClusterState,
    Mode,
    SegIcon,
    Telltale,
    Trip,
)

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

#: The LCD is a positive display: a light background with dark segments. Both
#: the manual's illustration and the photograph agree on this.
LCD_BG_TOP = "#f2f3ee"
LCD_BG_BOTTOM = "#dcdfd8"
INK = "#2f3a4a"  # the dark blue-grey of an active segment
INK_DIM = "#b9bdb6"  # an unlit segment, shown only in segment-test mode
GLASS = "#cfd3cb"

BEZEL = "#17181b"
BEZEL_EDGE = "#3a3d42"
BEZEL_HI = "#2a2c30"
WORDMARK = "#c8102e"  # TVS red

LAMP_OFF = "#2b2e33"
GREEN = "#35d67a"
AMBER = "#ffab1f"
RED = "#ff3b30"
BLUE = "#4aa8ff"

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

WIDTH = 1000
HEIGHT = 620

#: The display glass inside the surround.
GLASS_X, GLASS_Y = 100.0, 104.0
GLASS_W, GLASS_H = 800.0, 352.0

#: Pattern ids for the segment-text ink, normal and blinking.
INK_PATTERN = "lcd-ink"
WARN_PATTERN = "lcd-ink-warn"


def esc(text: str) -> str:
    return html.escape(text, quote=True)


# ---------------------------------------------------------------------------
# Seven-segment digits
# ---------------------------------------------------------------------------

#: Which segments make up each character. `a` top, `b` upper right, `c` lower
#: right, `d` bottom, `e` lower left, `f` upper left, `g` middle.
_SEGMENTS: dict[str, str] = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgedc",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
    "-": "g",
    "_": "d",
    " ": "",
}

#: Segment thickness and the clearance kept around every segment, both as a
#: fraction of the digit height. The clearance is what stops a lit segment from
#: running into its neighbour - without it a `0` renders as a solid block
#: rather than a ring.
_SEG_THICKNESS = 0.105
_SEG_GAP = 0.055
#: Digit width-to-height ratio. The cluster's digits are noticeably wider
#: than a classic 7-segment ratio, and at 0.58 a `0` came out looking like an
#: `8` because the top-right and bottom-right bars were too close together to
#: read as separate strokes.
_SEG_ASPECT = 0.74


def _h_segment(x0: float, x1: float, y: float, t: float) -> str:
    """A horizontal segment with pointed ends, as a real one is moulded."""
    return (
        f"{x0:.2f},{y:.2f} {x0 + t:.2f},{y - t:.2f} {x1 - t:.2f},{y - t:.2f} "
        f"{x1:.2f},{y:.2f} {x1 - t:.2f},{y + t:.2f} {x0 + t:.2f},{y + t:.2f}"
    )


def _v_segment(x: float, y0: float, y1: float, t: float) -> str:
    return (
        f"{x:.2f},{y0:.2f} {x + t:.2f},{y0 + t:.2f} {x + t:.2f},{y1 - t:.2f} "
        f"{x:.2f},{y1:.2f} {x - t:.2f},{y1 - t:.2f} {x - t:.2f},{y0 + t:.2f}"
    )


def digit_advance(height: float) -> float:
    """Horizontal pitch of one seven-segment character at this size."""
    return height * _SEG_ASPECT * 1.28


def seven_segment_width(text: str, height: float) -> float:
    """Total width [seven_segment] will occupy, for placing a label after it."""
    total = 0.0
    for char in text:
        if char == ".":
            total += height * 0.26
        elif char == ":":
            total += height * _SEG_ASPECT * 0.55
        else:
            total += digit_advance(height)
    return total


def seven_segment(
    text: str,
    x: float,
    y: float,
    height: float,
    *,
    color: str = INK,
    dim: str = INK_DIM,
    all_segments: bool = False,
    skew: float = -7.0,
) -> str:
    """Render [text] as seven-segment digits, right-skewed like the cluster's.

    [all_segments] draws the unlit segments faintly, which reproduces the
    manual's display-test illustration and makes it obvious how many cells a
    field actually has.
    """
    advance = digit_advance(height)
    w = height * _SEG_ASPECT
    t = height * _SEG_THICKNESS
    g = height * _SEG_GAP
    parts: list[str] = []
    cursor = 0.0

    for char in text:
        if char == ".":
            parts.append(
                f'<circle cx="{cursor + w * 0.30:.2f}" '
                f'cy="{height - g - t * 0.5:.2f}" r="{t * 0.62:.2f}" '
                f'fill="{color}"/>'
            )
            cursor += height * 0.26
            continue
        if char == ":":
            r = t * 0.55
            cx = cursor + w * 0.32
            parts.append(
                f'<circle cx="{cx:.2f}" cy="{height * 0.34:.2f}" '
                f'r="{r:.2f}" fill="{color}"/>'
            )
            parts.append(
                f'<circle cx="{cx:.2f}" cy="{height * 0.68:.2f}" '
                f'r="{r:.2f}" fill="{color}"/>'
            )
            cursor += w * 0.55
            continue

        lit = _SEGMENTS.get(char.upper(), "")
        # One translate per digit, with the segment points measured from that
        # digit's own origin.
        #
        # Two earlier versions of this were wrong. Drawing the points at
        # absolute coordinates and skewing the enclosing group shears about the
        # *canvas* origin, so a field placed at x=200 slid left by about a
        # third of its height and the ETA digits landed on the ETA label.
        # Folding the cursor into the point coordinates fixes the shear but
        # leaves every digit in a different coordinate space, which cannot be
        # read alongside its neighbours.
        #
        # A per-digit translate is the version that is both correct and legible:
        # the shear moves with the field, and each digit's geometry stays local
        # and identical.
        hx0, hx1 = g + t * 0.45, w - g - t * 0.45
        vy_top, vy_mid, vy_bot = g, height / 2 - t * 0.7, height - g
        vx_l, vx_r = g + t / 2, w - g - t / 2
        cells = {
            "a": _h_segment(hx0, hx1, g + t / 2, t),
            "g": _h_segment(hx0, hx1, height / 2, t),
            "d": _h_segment(hx0, hx1, height - g - t / 2, t),
            "f": _v_segment(vx_l, vy_top, vy_mid, t),
            "b": _v_segment(vx_r, vy_top, vy_mid, t),
            "e": _v_segment(vx_l, vy_mid, vy_bot, t),
            "c": _v_segment(vx_r, vy_mid, vy_bot, t),
        }
        digit = []
        for name, points in cells.items():
            if name not in lit and not all_segments:
                continue
            digit.append(
                f'<polygon points="{points}" '
                f'fill="{color if name in lit else dim}"/>'
            )
        if digit:
            parts.append(
                f'<g transform="translate({cursor:.2f},0)">' + "".join(digit) + "</g>"
            )
        cursor += advance

    # Position first, then shear, each about the origin it should apply to.
    transform = f"translate({x:.2f},{y:.2f})"
    if skew:
        transform += f" skewX({skew})"
    return f'<g transform="{transform}">' + "".join(parts) + "</g>"


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

SANS = "'DejaVu Sans','Helvetica Neue',Helvetica,Arial,sans-serif"
MONO = "'DejaVu Sans Mono','Menlo','Consolas',monospace"


def label(
    text: str,
    x: float,
    y: float,
    size: float = 13,
    *,
    color: str = INK,
    weight: str = "600",
    anchor: str = "start",
    italic: bool = False,
    family: str = SANS,
    fill: str | None = None,
    extra: str = "",
) -> str:
    style = "italic" if italic else "normal"
    paint = fill or color
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size:.1f}" '
        f'font-weight="{weight}" font-style="{style}" fill="{paint}" '
        f'text-anchor="{anchor}" {extra}>{esc(text)}</text>'
    )


#: The alpha-numeric display's own font breaks every stroke into dashes - it is
#: a segment LCD, and the photograph shows it clearly. A diagonal stripe
#: pattern reproduces that read without embedding a typeface.
def segment_text(
    text: str,
    x: float,
    y: float,
    size: float,
    *,
    color: str = INK,
    anchor: str = "start",
    cell: float = 0.0,
    pattern_id: str = INK_PATTERN,
) -> str:
    out = label(
        text,
        x,
        y,
        size,
        color=color,
        weight="600",
        anchor=anchor,
        family=MONO,
        fill=f"url(#{pattern_id})",
    )
    if cell:
        # A guide row: one faint box per character cell, which is how the
        # manual's display-test illustration shows the text area.
        guide = "".join(
            f'<rect x="{x + i * cell:.1f}" y="{y - size * 0.82:.1f}" '
            f'width="{cell - 2:.1f}" height="{size * 1.05:.1f}" '
            f'fill="none" stroke="{INK_DIM}" stroke-width="0.7" rx="2"/>'
            for i in range(len(text))
        )
        out = guide + out
    return out


# ---------------------------------------------------------------------------
# Small icons
# ---------------------------------------------------------------------------


def icon_bluetooth(x: float, y: float, size: float, color: str) -> str:
    h = size / 2
    w = size * 0.42
    return (
        f'<g stroke="{color}" stroke-width="{size * 0.11:.2f}" fill="none" '
        f'stroke-linejoin="round" stroke-linecap="round" '
        f'transform="translate({x:.1f},{y:.1f})">'
        f'<path d="M {w:.1f},{0:.1f} L {size - w:.1f},{h * 0.62:.1f} '
        f'L {w:.1f},{size:.1f} L {w:.1f},{0:.1f}"/>'
        f'<path d="M {w:.1f},{0:.1f} L {size - w:.1f},{h * 1.38:.1f} '
        f'L {w:.1f},{size:.1f}"/></g>'
    )


def icon_fuel_pump(x: float, y: float, size: float, color: str) -> str:
    w = size * 0.52
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<rect x="0" y="{size * 0.12:.1f}" width="{w:.1f}" height="{size * 0.82:.1f}" '
        f'rx="{size * 0.08:.1f}" fill="none" stroke="{color}" stroke-width="{size * 0.10:.1f}"/>'
        f'<rect x="{size * 0.12:.1f}" y="{size * 0.26:.1f}" width="{w * 0.45:.1f}" '
        f'height="{size * 0.22:.1f}" fill="{color}"/>'
        f'<path d="M {w + size * 0.06:.1f},{size * 0.36:.1f} '
        f'L {size * 0.82:.1f},{size * 0.30:.1f} L {size * 0.82:.1f},{size * 0.76:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="{size * 0.10:.1f}"/>'
        f'<rect x="{size * 0.68:.1f}" y="{size * 0.70:.1f}" width="{size * 0.24:.1f}" '
        f'height="{size * 0.24:.1f}" rx="{size * 0.04:.1f}" fill="{color}"/>'
        f"</g>"
    )


def icon_engine(x: float, y: float, size: float, color: str) -> str:
    """The malfunction indicator: an engine block with a fan on top."""
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<rect x="{size * 0.06:.1f}" y="{size * 0.42:.1f}" width="{size * 0.62:.1f}" '
        f'height="{size * 0.40:.1f}" rx="{size * 0.05:.1f}" fill="none" '
        f'stroke="{color}" stroke-width="{size * 0.09:.1f}"/>'
        f'<circle cx="{size * 0.34:.1f}" cy="{size * 0.26:.1f}" r="{size * 0.14:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="{size * 0.09:.1f}"/>'
        f'<circle cx="{size * 0.80:.1f}" cy="{size * 0.52:.1f}" r="{size * 0.13:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="{size * 0.09:.1f}"/>'
        f'<path d="M {size * 0.68:.1f},{size * 0.40:.1f} L {size * 0.46:.1f},{size * 0.40:.1f}" '
        f'stroke="{color}" stroke-width="{size * 0.09:.1f}"/>'
        f"</g>"
    )


def icon_high_beam(x: float, y: float, size: float, color: str) -> str:
    return (
        f'<g transform="translate({x:.1f},{y:.1f})" fill="{color}">'
        f'<path d="M {size * 0.86:.1f},{size * 0.06:.1f} '
        f'a {size * 0.86:.1f},{size * 0.86:.1f} 0 1,0 0,1 Z" opacity="0.0"/>'
        f'<path d="M {size * 0.92:.1f},{size * 0.10:.1f} '
        f'A {size * 0.90:.1f},{size * 0.90:.1f} 0 0,1 {size * 0.10:.1f},{size * 0.55:.1f} '
        f'L {size * 0.10:.1f},{size * 0.45:.1f} '
        f'A {size * 0.90:.1f},{size * 0.90:.1f} 0 0,1 {size * 0.92:.1f},{size * 0.10:.1f} Z"/>'
        f'<rect x="0" y="{size * 0.38:.1f}" width="{size * 0.34:.1f}" '
        f'height="{size * 0.24:.1f}" rx="{size * 0.05:.1f}"/>'
        f"</g>"
    )


def icon_battery(x: float, y: float, size: float, color: str, fill: float = 0.0) -> str:
    w = size * 0.74
    h = size * 0.44
    parts = [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{size * 0.06:.1f}" fill="none" stroke="{color}" stroke-width="{size * 0.07:.1f}"/>',
        f'<rect x="{x + w:.1f}" y="{y + h * 0.30:.1f}" width="{size * 0.08:.1f}" '
        f'height="{h * 0.40:.1f}" fill="{color}"/>',
    ]
    if fill > 0:
        inner = (w - size * 0.2) * min(1.0, fill)
        parts.append(
            f'<rect x="{x + size * 0.10:.1f}" y="{y + h * 0.24:.1f}" '
            f'width="{inner:.1f}" height="{h * 0.52:.1f}" fill="{color}"/>'
        )
    return "".join(parts)


def icon_signal(x: float, y: float, size: float, color: str, bars: int = 3) -> str:
    out = []
    for i in range(4):
        h = size * (0.28 + 0.24 * i)
        bar_w = size * 0.15
        x0 = x + i * size * 0.20
        on = i < bars
        out.append(
            f'<rect x="{x0:.1f}" y="{y + size - h:.1f}" width="{bar_w:.1f}" '
            f'height="{h:.1f}" fill="{color if on else INK_DIM}"/>'
        )
    return "".join(out)


def icon_phone(x: float, y: float, size: float, color: str) -> str:
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<path d="M {size * 0.10:.1f},{size * 0.06:.1f} '
        f'C {size * 0.02:.1f},{size * 0.34:.1f} {size * 0.30:.1f},{size * 0.98:.1f} '
        f'{size * 0.62:.1f},{size * 0.94:.1f} '
        f'C {size * 0.76:.1f},{size * 0.92:.1f} {size * 0.92:.1f},{size * 0.76:.1f} '
        f'{size * 0.92:.1f},{size * 0.62:.1f} '
        f'L {size * 0.72:.1f},{size * 0.52:.1f} '
        f'C {size * 0.66:.1f},{size * 0.46:.1f} {size * 0.60:.1f},{size * 0.48:.1f} '
        f'{size * 0.56:.1f},{size * 0.54:.1f} '
        f'C {size * 0.44:.1f},{size * 0.44:.1f} {size * 0.34:.1f},{size * 0.34:.1f} '
        f'{size * 0.26:.1f},{size * 0.22:.1f} '
        f'C {size * 0.32:.1f},{size * 0.16:.1f} {size * 0.34:.1f},{size * 0.10:.1f} '
        f'{size * 0.30:.1f},{size * 0.04:.1f} Z" fill="{color}"/>'
        f"</g>"
    )


def icon_envelope(x: float, y: float, size: float, color: str) -> str:
    return (
        f'<g transform="translate({x:.1f},{y:.1f})" fill="none" stroke="{color}" '
        f'stroke-width="{size * 0.10:.1f}" stroke-linejoin="round">'
        f'<rect x="0" y="{size * 0.18:.1f}" width="{size:.1f}" height="{size * 0.64:.1f}" '
        f'rx="{size * 0.06:.1f}"/>'
        f'<path d="M {size * 0.04:.1f},{size * 0.24:.1f} L {size * 0.5:.1f},{size * 0.56:.1f} '
        f'L {size * 0.96:.1f},{size * 0.24:.1f}"/></g>'
    )


def icon_helmet(x: float, y: float, size: float, color: str, bg: str = INK) -> str:
    """The helmet reminder. The manual shows it as a filled block."""
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<rect x="0" y="0" width="{size:.1f}" height="{size * 0.78:.1f}" '
        f'rx="{size * 0.14:.1f}" fill="{color}"/>'
        f'<path d="M {size * 0.12:.1f},{size * 0.78:.1f} '
        f'A {size * 0.38:.1f},{size * 0.38:.1f} 0 0,1 {size * 0.76:.1f},0 Z" '
        f'fill="{bg}"/>'
        f"</g>"
    )


def icon_spanner(x: float, y: float, size: float, color: str) -> str:
    return (
        f'<g transform="translate({x:.1f},{y:.1f}) rotate(-35 {size * 0.5:.1f} {size * 0.5:.1f})">'
        f'<path d="M {size * 0.08:.1f},{size * 0.30:.1f} '
        f'a {size * 0.20:.1f},{size * 0.20:.1f} 0 1,0 {size * 0.26:.1f},0 '
        f'l {size * 0.10:.1f},{size * 0.10:.1f} '
        f'a {size * 0.20:.1f},{size * 0.20:.1f} 0 1,1 -{size * 0.26:.1f},0 Z" fill="{color}"/>'
        f'<rect x="{size * 0.42:.1f}" y="{size * 0.44:.1f}" width="{size * 0.50:.1f}" '
        f'height="{size * 0.14:.1f}" rx="{size * 0.06:.1f}" fill="{color}"/>'
        f"</g>"
    )


def icon_thermometer(x: float, y: float, size: float, color: str) -> str:
    r = size * 0.17
    return (
        f'<g transform="translate({x:.1f},{y:.1f})" stroke="{color}" '
        f'stroke-width="{size * 0.09:.1f}" fill="none" stroke-linecap="round">'
        f'<path d="M {size * 0.38:.1f},{size * 0.10:.1f} v {size * 0.52:.1f}"/>'
        f'<circle cx="{size * 0.38:.1f}" cy="{size * 0.76:.1f}" r="{r:.1f}" fill="{color}"/>'
        f'<path d="M {size * 0.66:.1f},{size * 0.16:.1f} a {size * 0.22:.1f},{size * 0.22:.1f} 0 0,1 0,{size * 0.40:.1f}"/>'
        f'<path d="M {size * 0.80:.1f},{size * 0.26:.1f} a {size * 0.22:.1f},{size * 0.22:.1f} 0 0,1 0,{size * 0.40:.1f}"/>'
        f"</g>"
    )


def icon_oil(x: float, y: float, size: float, color: str) -> str:
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<path d="M {size * 0.08:.1f},{size * 0.34:.1f} '
        f'c {size * 0.22:.1f},-{size * 0.26:.1f} {size * 0.70:.1f},-{size * 0.26:.1f} '
        f'{size * 0.92:.1f},0 z" fill="{color}"/>'
        f'<path d="M {size * 0.20:.1f},{size * 0.34:.1f} '
        f'c {size * 0.02:.1f},-{size * 0.10:.1f} {size * 0.10:.1f},-{size * 0.16:.1f} '
        f'{size * 0.18:.1f},-{size * 0.20:.1f} '
        f'c {size * 0.06:.1f},{size * 0.06:.1f} {size * 0.02:.1f},{size * 0.14:.1f} '
        f'0,{size * 0.20:.1f} z" fill="{LCD_BG_TOP}"/>'
        f'<path d="M {size * 0.36:.1f},{size * 0.36:.1f} l {size * 0.16:.1f},0 '
        f'l 0,{size * 0.22:.1f} l {-size * 0.16:.1f},0 z" fill="{color}"/>'
        f"</g>"
    )


def icon_nav_pin(x: float, y: float, size: float, color: str) -> str:
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<path d="M {size * 0.5:.1f},{size * 0.04:.1f} '
        f'a {size * 0.36:.1f},{size * 0.36:.1f} 0 0,1 {size * 0.36:.1f},{size * 0.52:.1f} '
        f'c 0,{size * 0.28:.1f} -{size * 0.36:.1f},{size * 0.62:.1f} '
        f'-{size * 0.36:.1f},{size * 0.74:.1f} '
        f'c 0,-{size * 0.12:.1f} -{size * 0.36:.1f},-{size * 0.46:.1f} '
        f'-{size * 0.36:.1f},-{size * 0.74:.1f} '
        f'a {size * 0.36:.1f},{size * 0.36:.1f} 0 0,1 {size * 0.36:.1f},-{size * 0.52:.1f} z" '
        f'fill="{color}"/>'
        f'<circle cx="{size * 0.5:.1f}" cy="{size * 0.36:.1f}" r="{size * 0.13:.1f}" '
        f'fill="{LCD_BG_TOP}"/>'
        f"</g>"
    )


def icon_stopwatch(x: float, y: float, size: float, color: str) -> str:
    r = size * 0.38
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<circle cx="{size * 0.5:.1f}" cy="{size * 0.58:.1f}" r="{r:.1f}" fill="none" '
        f'stroke="{color}" stroke-width="{size * 0.08:.1f}"/>'
        f'<path d="M {size * 0.5:.1f},{size * 0.58:.1f} L {size * 0.5:.1f},{size * 0.30:.1f} '
        f'M {size * 0.5:.1f},{size * 0.58:.1f} L {size * 0.70:.1f},{size * 0.66:.1f}" '
        f'stroke="{color}" stroke-width="{size * 0.07:.1f}" stroke-linecap="round"/>'
        f'<rect x="{size * 0.38:.1f}" y="{size * 0.04:.1f}" width="{size * 0.24:.1f}" '
        f'height="{size * 0.10:.1f}" rx="{size * 0.04:.1f}" fill="{color}"/>'
        f"</g>"
    )


def icon_clock(x: float, y: float, size: float, color: str) -> str:
    return (
        f'<g transform="translate({x:.1f},{y:.1f})" fill="none" stroke="{color}" '
        f'stroke-width="{size * 0.09:.1f}" stroke-linecap="round">'
        f'<circle cx="{size * 0.5:.1f}" cy="{size * 0.5:.1f}" r="{size * 0.44:.1f}"/>'
        f'<path d="M {size * 0.5:.1f},{size * 0.22:.1f} L {size * 0.5:.1f},{size * 0.52:.1f} '
        f'L {size * 0.70:.1f},{size * 0.62:.1f}"/></g>'
    )


# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------


def gauge_bars(
    x: float,
    y: float,
    width: float,
    height: float,
    lit: int,
    *,
    total: int = GAUGE_BARS,
    color: str = INK,
    blink_last: bool = False,
) -> str:
    """The cluster's bar gauges: a leaning ladder of horizontal bars.

    Both the fuel gauge and the temperature gauge are drawn as this, leaning
    the same way, which is what the manual's illustration shows. [blink_last]
    is the manual's low-fuel behaviour - at reserve the final bar blinks.
    """
    parts = []
    gap = height * 0.06
    bar_h = (height - gap * (total - 1)) / total
    lean = width * 0.34
    for i in range(total):
        t = i / max(1, total - 1)
        y0 = y + height - bar_h - i * (bar_h + gap)
        x0 = x + lean * (1.0 - t)
        on = i < lit
        fill = color if on else INK_DIM
        if on and blink_last and i == lit - 1:
            # The manual's low-fuel and over-temperature behaviour: the last
            # bar blinks rather than sitting steady.
            fill = f"url(#{WARN_PATTERN})"
        parts.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{width:.1f}" height="{bar_h:.1f}" '
            f'rx="1.2" fill="{fill}"/>'
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# The whole face
# ---------------------------------------------------------------------------


@dataclass
class RenderOptions:
    """Knobs the web display and the CLI both expose."""

    #: Draw every segment faintly, as a real LCD does and as the manual's
    #: display-test illustration shows. This is the mode that makes the text
    #: area's real width visible.
    all_segments: bool = False
    #: Mark the simulator's own decisions on the face: the navigation zone's
    #: placement, and where the text area ends.
    show_debug_overlay: bool = False
    #: Force the ignition-on self-check look.
    segment_test: bool = False


def render_svg(state: ClusterState, options: RenderOptions | None = None) -> str:
    """Render the cluster face as a standalone SVG document."""
    opt = options or RenderOptions()

    body = [
        _defs(),
        _surround(state),
        f'<g transform="translate({GLASS_X:.0f},{GLASS_Y:.0f})">',
        _glass_background(),
        _top_row(state, opt),
        _second_row(state, opt),
        _speed(state, opt),
        _status_line(state, opt),
        _bottom_row(state, opt),
        _text_area(state, opt),
        "</g>",
        _lamps(state),
        _wordmark(),
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" '
        f'font-family="{SANS}">'
        f'<title>TVS Ntorq instrument cluster - digital clone</title>'
        + "".join(body)
        + "</svg>"
    )


def _defs() -> str:
    """The diagonal stripe that gives the text its segment-LCD texture."""
    return (
        "<defs>"
        '<linearGradient id="lcd" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{LCD_BG_TOP}"/>'
        f'<stop offset="1" stop-color="{LCD_BG_BOTTOM}"/>'
        "</linearGradient>"
        f'<pattern id="{INK_PATTERN}" patternUnits="userSpaceOnUse" width="7" height="7" '
        'patternTransform="rotate(-28)">'
        f'<rect width="7" height="7" fill="{INK}"/>'
        f'<rect width="2.1" height="7" fill="{LCD_BG_TOP}" opacity="0.85"/>'
        "</pattern>"
        f'<pattern id="{WARN_PATTERN}" patternUnits="userSpaceOnUse" width="7" height="7" '
        'patternTransform="rotate(-28)">'
        f'<rect width="7" height="7" fill="{INK}"/>'
        f'<rect width="2.1" height="7" fill="{LCD_BG_TOP}" opacity="0.85"/>'
        "</pattern>"
        "</defs>"
    )


def _glass_background() -> str:
    return (
        f'<rect x="0" y="0" width="{GLASS_W:.0f}" height="{GLASS_H:.0f}" '
        f'fill="url(#lcd)"/>'
    )


def _surround(state: ClusterState) -> str:
    """The moulded surround the display sits in."""
    return (
        f'<path d="M 92,86 L 908,86 Q 950,86 962,126 L 990,470 '
        f'Q 996,528 944,540 L 806,556 Q 500,572 194,556 L 56,540 '
        f'Q 4,528 10,470 L 38,126 Q 50,86 92,86 Z" fill="{BEZEL}"/>'
        f'<path d="M 92,86 L 908,86 Q 950,86 962,126 L 990,470 '
        f'Q 996,528 944,540 L 806,556 Q 500,572 194,556 L 56,540 '
        f'Q 4,528 10,470 L 38,126 Q 50,86 92,86 Z" fill="none" '
        f'stroke="{BEZEL_EDGE}" stroke-width="3"/>'
        f'<rect x="{GLASS_X - 8:.0f}" y="{GLASS_Y - 8:.0f}" width="{GLASS_W + 16:.0f}" '
        f'height="{GLASS_H + 16:.0f}" rx="6" fill="{BEZEL_HI}"/>'
        f'<rect x="{GLASS_X:.0f}" y="{GLASS_Y:.0f}" width="{GLASS_W:.0f}" '
        f'height="{GLASS_H:.0f}" fill="url(#lcd)"/>'
    )


def _top_row(state: ClusterState, opt: RenderOptions) -> str:
    """Trip distance, odometer and clock - manual 1a, 1b and 1k."""
    trip = _trip_text(state)
    trip_w = seven_segment_width(trip, 30)
    odo = str(int(state.odometer_km()))
    odo_w = seven_segment_width(odo, 34)

    return "".join(
        [
            label("TRIP", 14, 22, 12, italic=True),
            _trip_letter(state),
            seven_segment(trip, 14, 32, 30, all_segments=opt.all_segments),
            label("KM", 18 + trip_w, 58, 10),
            # The trip selector. The manual's illustration shows `LAP`, `TRIP`
            # and then the F/A/B choices; the selected one is filled. Which
            # trip is selected arrives in `5A 10` index 8 as SELECTED_TRIP,
            # and the app's `Session` reads it, so the box is driven by the
            # same value the phone decodes rather than by display-only state.
            _trip_selector(state),
            # The diagonal rule the manual draws between the trip zone and the
            # odometer zone.
            f'<path d="M 208,4 L 172,72" stroke="{INK}" stroke-width="1.6" fill="none"/>',
            label("ODO", 178, 68, 11, italic=True),
            seven_segment(odo, 216, 16, 34, all_segments=opt.all_segments),
            label("KM", 222 + odo_w, 44, 11),
            _clock_box(state, opt),
        ]
    )


def _trip_letter(state: ClusterState) -> str:
    """The filled letter box beside the `TRIP` label.

    Shown for `TRIP A`, `TRIP B` and `TRIP F` only. `ODO` is not a trip - it is
    the default reading, and the manual's illustration labels it as a separate
    word to the right of the divider rather than as a choice in the box. Putting
    `ODO` in an 18-pixel box drew it spilling over both edges.
    """
    if state.selected_trip == Trip.ODO:
        return ""
    letter = state.selected_trip.value.upper()
    return (
        f'<rect x="56" y="8" width="18" height="16" fill="{INK}"/>'
        + label(letter, 65, 20.5, 11, color=LCD_BG_TOP, anchor="middle", weight="700")
    )


def _trip_selector(state: ClusterState) -> str:
    """The F / A / B trip choices, with the selected one filled.

    Manual section 1k: in street mode `TRIP A` shows by default, the mode
    button steps to `TRIP B`, and `TRIP F` cannot be selected manually - it
    appears on its own at reserve fuel and the display switches to it after
    five seconds. TRIP F is therefore drawn dimmer: it is not a choice.
    """
    parts = []
    for index, letter in enumerate(("F", "A", "B")):
        x = 92 + index * 15
        selected = state.selected_trip.value.upper() == letter
        manual = letter != "F"
        parts.append(
            f'<rect x="{x}" y="9" width="13" height="14" rx="2" '
            f'fill="{INK if selected else "none"}" '
            f'stroke="{INK if selected or manual else INK_DIM}" '
            f'stroke-width="1.2"'
            + ("" if selected or manual else ' stroke-dasharray="2 2"')
            + "/>"
        )
        parts.append(
            label(
                letter,
                x + 6.5,
                20,
                9,
                color=LCD_BG_TOP if selected else (INK if manual else INK_DIM),
                anchor="middle",
                weight="700",
            )
        )
    return "".join(parts)


def _trip_text(state: ClusterState) -> str:
    tenths = state.selected_trip_tenths()
    if state.selected_trip == Trip.ODO:
        return str(tenths // 10)
    return f"{tenths // 10}.{tenths % 10}"


def _clock_box(state: ClusterState, opt: RenderOptions) -> str:
    """Manual 1b: 12-hour, with AM/PM."""
    clock = state.clock()
    hours = clock.hour % 12 or 12
    text = f"{hours:02d}:{clock.minute:02d}"
    meridiem = "AM" if clock.hour < 12 else "PM"

    box_x, box_w = 552.0, 186.0
    digits_w = seven_segment_width(text, 26)
    x = box_x + (box_w - digits_w - 34) / 2

    return "".join(
        [
            f'<rect x="{box_x:.0f}" y="6" width="{box_w:.0f}" height="62" rx="8" '
            f'fill="none" stroke="{INK}" stroke-width="1.8"/>',
            seven_segment(text, x, 16, 26, all_segments=opt.all_segments),
            icon_clock(x + digits_w + 8, 16, 18, INK),
            label(meridiem, x + digits_w + 8, 52, 9),
        ]
    )


def _second_row(state: ClusterState, opt: RenderOptions) -> str:
    """Bluetooth, side stand, the phone's own icons, and the secondary readout."""
    connected = SegIcon.BLUETOOTH in state.segment_icons
    ink = INK if connected else INK_DIM

    parts = [
        icon_bluetooth(14, 82, 18, ink),
        # Manual 1m. Drawn dashed and empty when the stand is up, which is how
        # the manual's illustration shows it.
        f'<rect x="46" y="80" width="58" height="24" rx="4" fill="none" '
        f'stroke="{INK if state.side_stand_lit else INK_DIM}" '
        f'stroke-width="1.6"'
        + ("" if state.side_stand_lit else ' stroke-dasharray="3 3"')
        + "/>",
        label("SIDE", 50, 89, 8, color=INK if state.side_stand_lit else INK_DIM),
        label("STAND", 50, 99, 8, color=INK if state.side_stand_lit else INK_DIM),
    ]

    if SegIcon.PHONE_BATTERY in state.segment_icons:
        parts.append(icon_battery(116, 86, 20, INK, state.phone_battery_bars / 5.0))
    if SegIcon.SIGNAL in state.segment_icons:
        parts.append(icon_signal(148, 82, 20, INK, state.phone_signal_bars))

    # The right-hand secondary element: the 0-60 timer in sport mode, top
    # speed otherwise. Both are documented display elements.
    if state.mode == Mode.SPORT:
        heading, unit = "0-60", "S"
        # Manual 2c: the element disappears when the bike is not moving, and
        # shows the default 15 s rather than a real time when a run exceeded
        # that. Showing a real `0.0` because no run has been timed yet would
        # be inventing a measurement.
        value = (
            f"{state.shortest_time_display():.1f}"
            if state.shortest_time_display() > 0
            else ""
        )
    else:
        heading, unit = "TOP SPEED", "km/h"
        value = str(int(round(state.top_speed_kmh)))
    value_w = seven_segment_width(value, 22)
    parts.append(label(heading, 786, 84, 11, italic=True, anchor="end"))
    if value:
        parts.append(
            seven_segment(
                value, 786 - value_w, 90, 22, all_segments=opt.all_segments
            )
        )
        parts.append(label(unit, 786 - value_w - 8, 108, 9, anchor="end"))
    return "".join(parts)


def _speed(state: ClusterState, opt: RenderOptions) -> str:
    """Manual 1d: the speedometer, in large central digits."""
    value = int(round(state.speed_kmh))
    # The speedometer is a three-digit field, and a field that is not full goes
    # **blank** on the left rather than reading `062`. Zero-padding would put a
    # leading zero in front of every speed under a hundred: wrong to look at,
    # and a different reading than the dash gives. A space in the string
    # renders no segments but still advances the cursor, which is how that
    # position stays dark.
    digits = f"{value:3d}" if value < 1000 else str(value)
    width = seven_segment_width(digits, 112)
    x = 400 - width / 2
    return "".join(
        [
            seven_segment(digits, x, 122, 112, all_segments=opt.all_segments, skew=-9.0),
            label("km/h", x + width + 10, 238, 14, italic=True),
        ]
    )


def _status_line(state: ClusterState, opt: RenderOptions) -> str:
    """The line above the text rows.

    When navigation is live this carries the maneuver the phone sent: the
    arrow, the distance to the turn, the remaining time and the remaining
    distance. **Where it sits is a simulator decision, not an observation** -
    the cluster's navigation zone has never been seen carrying a live route.
    The debug overlay says so on the face, where someone debugging the app
    will actually see it, rather than only in a comment.
    """
    nav = state.nav
    # The navigation strip takes the status-line slot. When it is up the
    # alpha-numeric rows below keep their own space and show nothing, rather
    # than two zones drawing over the same pixels - an overlap like that is a
    # simulator artefact, and the cluster would not do it.
    if not nav.active:
        if state.mode == Mode.STREET:
            return ""
        return label(state.mode.label, 400, 288, 15, color=INK_DIM, anchor="middle")

    # A filled band rather than an outline, so the strip reads as a distinct zone
    # and the two text rows below it are not left half-visible.
    box = (
        f'<rect x="140" y="256" width="472" height="46" rx="6" '
        f'fill="{LCD_BG_TOP}" stroke="{INK}" stroke-width="1.0" opacity="0.75"'
        + (' stroke-dasharray="4 4"' if opt.show_debug_overlay else "")
        + "/>"
    )

    # The distance-to-turn and the ETA each carry their own unit, so both are
    # formatted as a string rather than as digits beside a separate label. The
    # `5A 4E` layout says [2..3] is metres and [4..5] is minutes, and the
    # reference implementation's alternating line shows `850m`, `12km`, `45m`,
    # `1h 20m` - one string, not a number and a unit bolted on.
    distance = _format_distance(nav.distance_m)
    eta = _format_duration(nav.eta_minutes)
    remaining = _format_distance(nav.remaining_m)

    parts = [
        box,
        _pictogram(nav.pictogram, 152, 262, 34, INK),
        seven_segment(distance, 200, 264, 26, all_segments=opt.all_segments),
        label("ETA", 316, 286, 11, italic=True),
        seven_segment(eta, 348, 264, 26, all_segments=opt.all_segments),
        label(remaining, 602, 286, 13, anchor="end"),
    ]
    return "".join(parts)


def _format_duration(minutes: int) -> str:
    """Remaining time, as the reference implementation writes it.

    `"1h 20m"`, `"45m"`. Under an hour it is just minutes. The space is a real
    space because the cluster's text field is ASCII-only and the same sanitiser
    the phone applies would fold anything else.
    """
    if minutes < 60:
        return f"{max(0, minutes)}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _pictogram(pictogram_id: int, x: float, y: float, size: float, color: str) -> str:
    paths = pictograms.svg_paths(pictogram_id)
    scale = size / pictograms.BOX
    body = []
    for data, filled in paths:
        body.append(
            f'<path d="{data}" fill="{color if filled else "none"}" '
            f'stroke="{color if not filled else "none"}" '
            f'stroke-width="{pictograms.STROKE}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )
    unknown = (
        ""
        if pictograms.for_id(pictogram_id)
        else f'<text x="{x + size + 5:.0f}" y="{y + size * 0.8:.0f}" font-size="10" '
        f'fill="{RED}" font-family="{SANS}">no glyph for {pictogram_id}</text>'
    )
    return (
        f'<g transform="translate({x:.1f},{y:.1f}) scale({scale:.4f})">'
        + "".join(body)
        + "</g>"
        + unknown
    )


def _format_distance(metres: int) -> str:
    """The reference app's right-aligned forms: 850m, 12km, 120k."""
    if metres <= 0:
        return "0m"
    if metres >= 100_000:
        return f"{metres // 1000}k"
    if metres >= 1_000:
        return f"{metres // 1000}km"
    return f"{metres}m"


def _bottom_row(state: ClusterState, opt: RenderOptions) -> str:
    """The temperature gauge, the navigation pin, and the fuel gauge."""
    parts: list[str] = []

    # Manual 1e: engine temperature, six bars, H at the top and C at the
    # bottom, with the thermometer and coolant symbols beside it.
    parts.extend(
        [
            icon_thermometer(10, 310, 22, INK),
            icon_oil(36, 314, 20, INK),
            gauge_bars(64, 310, 24, 40, state.temp_bars(),
                       blink_last=state.temp_over_range()),
            label("H", 58, 308, 11, italic=True, anchor="end"),
            label("C", 58, 352, 11, italic=True, anchor="end"),
        ]
    )
    if SegIcon.LOW_BATTERY in state.segment_icons:
        parts.append(icon_battery(10, 334, 16, INK, 0.12))
    if SegIcon.SERVICE in state.segment_icons:
        parts.append(icon_spanner(34, 332, 18, INK))

    # Manual 1c: fuel, six bars, F at the top and E at the bottom, with the
    # pump symbol beside it.
    parts.extend(
        [
            gauge_bars(700, 310, 24, 40, state.fuel_bars(),
                       blink_last=state.fuel_at_reserve()),
            label("F", 732, 308, 11, italic=True),
            label("E", 732, 352, 11, italic=True),
            icon_fuel_pump(742, 312, 22, INK),
        ]
    )
    if SegIcon.ENVELOPE in state.segment_icons:
        parts.append(icon_envelope(742, 338, 18, INK))
    if SegIcon.NAV_PIN in state.segment_icons or state.nav.active:
        parts.append(icon_nav_pin(584, 322, 20, INK))

    return "".join(parts)


#: Where the text area starts, and how wide one character cell is. The
#: individual line capacities are in `TEXT_ROW_CAPACITIES`.
TEXT_X0 = 110.0
TEXT_CELL = 36.0
TEXT_BASELINES = (318.0, 342.0)


def _text_area(state: ClusterState, opt: RenderOptions) -> str:
    """The alpha-numeric display: ten characters on line 1, twelve on line 2.

    This is the zone that matters most. The manual's display-test illustration
    shows two unequal lines: ten cells on line 1 and twelve on line 2. The
    debug overlay draws the per-line cell grid so that difference is visible.
    """
    parts: list[str] = []

    if state.nav.active:
        # The navigation strip has the lower band while a route is live.
        #
        # Whether the real cluster's HUD shares the alpha-numeric display or
        # sits above it is not established by the manual, which lists the text
        # area only as "vehicle information and Smart-connect related messages"
        # and says nothing about a navigation zone. Drawing both at once would
        # have two things claiming the same glass, which is a simulator artefact
        # rather than a property of the vehicle - so one is suppressed, and the
        # suppression is stated in the overlay rather than left to be guessed.
        if opt.show_debug_overlay:
            parts.append(
                label(
                    "navigation strip has this band; alpha-numeric display "
                    "suppressed",
                    TEXT_X0, 312, 9, color=INK_DIM, weight="500",
                )
            )
        return "".join(parts)

    message = state.message.text if state.message else ""
    lines, dropped = _wrap_to_cells(message)

    for row in range(TEXT_ROWS):
        capacity = TEXT_ROW_CAPACITIES[row]
        text = lines[row][:capacity] if row < len(lines) else ""
        parts.append(
            segment_text(
                text,
                TEXT_X0,
                TEXT_BASELINES[row],
                22,
                cell=TEXT_CELL
                if (opt.show_debug_overlay or opt.all_segments)
                else 0.0,
            )
        )

    # Text the cluster physically cannot show. Always reported, not only in the
    # debug overlay: a message silently vanishing is exactly the kind of thing
    # this simulator exists to make impossible to miss.
    #
    # Positioned to the right of the second row, where there is free glass. It
    # sits above the first row instead when the top row is empty, so it is not
    # drawn over the text itself.
    if dropped:
        note_x = TEXT_X0 + TEXT_CELL * max(TEXT_ROW_CAPACITIES) + 6
        if not lines or not lines[0]:
            note_y = TEXT_BASELINES[1]
        else:
            note_y = TEXT_BASELINES[0] - 26
        parts.append(
            label(
                f"+{dropped} dropped",
                note_x,
                note_y,
                11,
                color=RED,
                weight="700",
            )
        )

    if opt.show_debug_overlay:
        # Below the gauges, where nothing else sits, so the annotation cannot be
        # mistaken for a cluster element.
        note = "line 1 = 10 cells; line 2 = 12 cells"
        if dropped:
            note += f"; {dropped} character(s) have nowhere to go"
        parts.append(
            label(note, TEXT_X0, 264, 9,
                  color=RED if dropped else INK_DIM, weight="500")
        )
    return "".join(parts)


def _wrap_to_cells(text: str, *, start_line: int = 0) -> tuple[list[str], int]:
    """Split a message across line 1 (10 cells) and line 2 (12 cells).

    The display has unequal line widths. Word wrapping keeps a phone number
    intact where possible; excess after both line capacities is reported.

    If the sender targets line 2 directly, anything past twelve characters is
    dropped. Whole-message mode reports overflow beyond both lines' capacity.
    A message too long for the dash is a fact about the cluster, not something
    to paper over with a marquee: the thing that matters when debugging is
    whether the phone is sending more text than the display has room for, and
    scrolling it away would hide the finding rather than report it.
    """
    if not 0 <= start_line < TEXT_ROWS:
        raise ValueError(f"start_line must be in 0..{TEXT_ROWS - 1}")
    if not text:
        return [], 0
    lines: list[str] = []
    remaining = text
    line_index = start_line
    while remaining and line_index < TEXT_ROWS:
        capacity = TEXT_ROW_CAPACITIES[line_index]
        if len(remaining) <= capacity:
            lines.append(remaining)
            remaining = ""
            break
        cut = remaining.rfind(" ", 0, capacity + 1)
        if cut <= 0:
            cut = capacity
        lines.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
        line_index += 1
    return lines, len(remaining)


# ---------------------------------------------------------------------------
# The surround
# ---------------------------------------------------------------------------

#: Lamps on the moulding, left column then right, with their manual numbers.
BEZEL_SLOTS: tuple[tuple[str, float, float], ...] = (
    ("turn-left", 62.0, 150.0),
    ("high-beam", 62.0, 224.0),
    ("eco", 44.0, 298.0),
    ("power", 44.0, 298.0),
    ("turn-right", 908.0, 150.0),
    ("low-fuel", 902.0, 224.0),
    ("mil", 902.0, 298.0),
    ("notify", 902.0, 372.0),
)


def _lamps(state: ClusterState) -> str:
    """Manual page 25, items 1-7: every lamp the manual lists.

    Two of them, eco and power, share one moulded unit, so they are drawn as a
    pair rather than as two separate lamps.
    """
    parts: list[str] = []
    eco_on = Telltale.ECO in state.telltales
    power_on = Telltale.POWER in state.telltales

    def lamp(color: str) -> str:
        return color

    # Left column.
    parts.append(
        _arrow_icon(46, 138, 30, GREEN if Telltale.TURN_LEFT in state.telltales else LAMP_OFF, -1)
    )
    parts.append(
        icon_high_beam(46, 210, 28, BLUE if Telltale.HIGH_BEAM in state.telltales else LAMP_OFF)
    )
    parts.append(label("ECO", 34, 292, 9, color=LAMP_OFF, weight="700"))
    parts.append(label("PWR", 34, 306, 9, color=LAMP_OFF, weight="700"))
    parts.append(
        f'<rect x="66" y="284" width="16" height="11" rx="2" '
        f'fill="{lamp(GREEN if eco_on else LAMP_OFF)}"/>'
    )
    parts.append(
        f'<rect x="66" y="298" width="16" height="11" rx="2" '
        f'fill="{lamp(AMBER if power_on else LAMP_OFF)}"/>'
    )

    # Right column.
    parts.append(
        _arrow_icon(926, 138, 30, GREEN if Telltale.TURN_RIGHT in state.telltales else LAMP_OFF, 1)
    )
    parts.append(
        icon_fuel_pump(
            900, 212, 30, AMBER if Telltale.LOW_FUEL in state.telltales else LAMP_OFF
        )
    )
    parts.append(
        icon_engine(900, 286, 30, RED if Telltale.MIL in state.telltales else LAMP_OFF)
    )
    parts.append(
        f'<circle cx="914" cy="378" r="9" '
        f'fill="{GREEN if Telltale.NOTIFY in state.telltales else LAMP_OFF}"/>'
    )
    return "".join(parts)


def _arrow_icon(x: float, y: float, size: float, color: str, direction: int) -> str:
    """A turn-signal arrow. [direction] is -1 for left, 1 for right."""
    tip = x + size if direction > 0 else x
    tail = x if direction > 0 else x + size
    head = " ".join(
        [
            f"M {tip:.1f},{y + size * 0.5:.1f}",
            f"L {tip - direction * size * 0.38:.1f},{y + size * 0.06:.1f}",
            f"L {tip - direction * size * 0.38:.1f},{y + size * 0.28:.1f}",
            f"L {tail:.1f},{y + size * 0.28:.1f}",
            f"L {tail:.1f},{y + size * 0.72:.1f}",
            f"L {tip - direction * size * 0.38:.1f},{y + size * 0.72:.1f}",
            f"L {tip - direction * size * 0.38:.1f},{y + size * 0.94:.1f}",
            "Z",
        ]
    )
    return f'<path d="{head}" fill="{color}"/>'


def _wordmark() -> str:
    return (
        label(
            "NTORQ",
            WIDTH / 2,
            512,
            34,
            color=WORDMARK,
            weight="800",
            anchor="middle",
        )
        + label("MODE", 360, 556, 11, color="#6b6f76", weight="700", anchor="middle")
        + label("SET", 640, 556, 11, color="#6b6f76", weight="700", anchor="middle")
    )
