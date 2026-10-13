# Maneuver pictograms, drawn as SVG paths.
#
# The id -> label table is transcribed from TVS Connect v8.14.1,
# `com.tvsm.connect.utils.ManeuverUtils.getShortInstruction()`, and is the
# same table as `app/src/main/java/dev/tvsopen/protocol/Pictogram.kt` in
# TVSRideCompanion. That method's second branch - reached when the vehicle
# type is anything other than "22" - is what is reproduced here, for the
# reason the Kotlin file gives: it is the vendor's own current mapping and the
# best available candidate for the Ntorq.
#
# The glyphs themselves are the simulator's own drawing. They are not traced
# from the cluster and they are not what the dash will show: which glyph the
# Ntorq renders for a given byte is one of the open questions in
# TVSRideCompanion/docs/NAVIGATION.md. What this module guarantees is that the
# *byte* round-trips and that an id with no glyph is visibly missing rather
# than silently defaulting.

from __future__ import annotations

import math
from dataclasses import dataclass

#: Drawing box for one pictogram. Every path below fits in this.
BOX = 100.0

#: Stroke weight for the arrow shafts.
STROKE = 9.0


@dataclass(frozen=True)
class Pictogram:
    """One cluster pictogram byte."""

    id: int
    label: str
    #: Roundabout exit bearings are an angle in degrees; others are None.
    roundabout_exit_degrees: int | None = None


#: The official app's table, in its own order.
ALL: tuple[Pictogram, ...] = (
    # basic turns
    Pictogram(0, "turn_left"),
    Pictogram(1, "sharp_left_turn"),
    Pictogram(2, "slight_left"),
    Pictogram(3, "turn_right"),
    Pictogram(4, "sharp_right_turn"),
    Pictogram(5, "slight_right"),
    Pictogram(6, "make_a_u_turn"),
    Pictogram(7, "continue_straight"),
    Pictogram(8, "destination"),
    # slip roads
    Pictogram(9, "exit_highway"),
    Pictogram(10, "enter_highway"),
    # junctions and forks
    Pictogram(11, "take_next_left"),
    Pictogram(12, "take_next_right"),
    Pictogram(13, "left_at_junction"),
    Pictogram(14, "right_at_junction"),
    Pictogram(15, "left_at_fork"),
    Pictogram(16, "right_at_fork"),
    Pictogram(17, "sharp_left_at_junciton"),  # spelling is upstream's
    Pictogram(18, "sharp_right_at_junciton"),
    Pictogram(19, "take_left_at_junction"),
    Pictogram(20, "take_right_at_junction"),
    Pictogram(21, "continue_straight"),  # a duplicate of 7 upstream
    Pictogram(22, "continue_left"),
    Pictogram(23, "left_after_right_curve"),
    Pictogram(24, "continue_right"),
    Pictogram(25, "right_after_left_curve"),
    # ferries
    Pictogram(36, "take_ferry"),
    Pictogram(37, "leave_ferry"),
    # compass bearings
    Pictogram(50, "head_north"),
    Pictogram(51, "head_northeast"),
    Pictogram(52, "head_east"),
    Pictogram(53, "head_southeast"),
    Pictogram(54, "head_south"),
    Pictogram(55, "head_southwest"),
    Pictogram(56, "head_west"),
    Pictogram(57, "head_northwest"),
    # roundabout exits. India drives left, so these run clockwise.
    Pictogram(65, "roundabout_exit_at_45", 45),
    Pictogram(66, "roundabout_exit_at_90", 90),
    Pictogram(67, "roundabout_exit_at_135", 135),
    Pictogram(68, "roundabout_exit_at_180", 180),
    Pictogram(69, "roundabout_exit_at_225", 225),
    Pictogram(70, "roundabout_exit_at_270", 270),
    Pictogram(71, "roundabout_exit_at_315", 315),
    # highway, which upstream reaches through the default branch
    Pictogram(34, "exit_highway"),
    Pictogram(35, "enter_highway"),
)

#: `41` is special-cased twice upstream, ahead of the switch, and in both
#: vehicle-type branches - evidence it is a U-turn that was later given its
#: own id.
U_TURN_LEGACY = Pictogram(41, "make_a_u_turn")

_BY_ID = {p.id: p for p in ALL}
_BY_ID[U_TURN_LEGACY.id] = U_TURN_LEGACY

#: Every id the table knows, for a calibration-style picker.
IDS: list[int] = sorted(_BY_ID)

#: Id the app sends for a maneuver it could not resolve. A wrong-but-plausible
#: arrow is worse than an honest straight-on, and the text line carries the
#: real instruction regardless.
STRAIGHT = 7


def for_id(pictogram_id: int) -> Pictogram | None:
    return _BY_ID.get(pictogram_id)


def label(pictogram_id: int) -> str:
    found = for_id(pictogram_id)
    return found.label if found else f"unknown_{pictogram_id}"


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _points(*pairs: tuple[float, float]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pairs)


def _arrow_head(x: float, y: float, direction_deg: float, size: float = 20.0) -> str:
    """A closed triangular head pointing along [direction_deg].

    Screen coordinates: 0 degrees is east, and y grows downward, so a "north"
    heading is -90.
    """
    rad = math.radians(direction_deg)
    tip = (x, y)
    left = (
        x - size * math.cos(rad) + size * 0.55 * math.sin(rad),
        y - size * math.sin(rad) - size * 0.55 * math.cos(rad),
    )
    right = (
        x - size * math.cos(rad) - size * 0.55 * math.sin(rad),
        y - size * math.sin(rad) + size * 0.55 * math.cos(rad),
    )
    return f"M {tip[0]:.1f},{tip[1]:.1f} L {left[0]:.1f},{left[1]:.1f} L {right[0]:.1f},{right[1]:.1f} Z"


def _polyline(points: list[tuple[float, float]]) -> str:
    return "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def svg_paths(pictogram_id: int) -> list[tuple[str, bool]]:
    """Return `(path_data, filled)` pairs for the given pictogram byte.

    An id the table does not know yields a single unmistakable "no glyph"
    marker rather than a plausible-looking arrow: a wrong arrow on a dash is
    worse than an obviously missing one.
    """
    pic = for_id(pictogram_id)
    if pic is None:
        return [
            ("M 50,12 L 50,50", False),
            (_arrow_head(50, 78, -90), True),
            ("M 30,50 L 70,50", False),
        ]

    if pic.roundabout_exit_degrees is not None:
        return _roundabout(pic.roundabout_exit_degrees)

    name = pic.label
    drawer = _DRAWERS.get(name)
    if drawer is None:
        return [
            ("M 50,12 L 50,50", False),
            (_arrow_head(50, 78, -90), True),
            ("M 30,50 L 70,50", False),
        ]
    return drawer()


def _straight() -> list[tuple[str, bool]]:
    return [(_polyline([(50, 74), (50, 26)]), False), (_arrow_head(50, 18, -90), True)]


def _turn_left() -> list[tuple[str, bool]]:
    return [(_polyline([(62, 74), (62, 48), (38, 48), (38, 24)]), False),
            (_arrow_head(38, 16, -90), True)]


def _turn_right() -> list[tuple[str, bool]]:
    return [(_polyline([(38, 74), (38, 48), (62, 48), (62, 24)]), False),
            (_arrow_head(62, 16, -90), True)]


def _slight_left() -> list[tuple[str, bool]]:
    return [(_polyline([(58, 74), (50, 56), (42, 40), (40, 26)]), False),
            (_arrow_head(40, 18, -100), True)]


def _slight_right() -> list[tuple[str, bool]]:
    return [(_polyline([(42, 74), (50, 56), (58, 40), (60, 26)]), False),
            (_arrow_head(60, 18, -80), True)]


def _sharp_left() -> list[tuple[str, bool]]:
    return [(_polyline([(66, 74), (66, 52), (34, 52), (34, 20)]), False),
            (_arrow_head(34, 12, -90), True)]


def _sharp_right() -> list[tuple[str, bool]]:
    return [(_polyline([(34, 74), (34, 52), (66, 52), (66, 20)]), False),
            (_arrow_head(66, 12, -90), True)]


def _u_turn() -> list[tuple[str, bool]]:
    return [
        (_polyline([(36, 78), (36, 42), (64, 42), (64, 66)]), False),
        (_arrow_head(64, 76, 90), True),
    ]


def _destination() -> list[tuple[str, bool]]:
    return [
        (_polyline([(50, 74), (50, 30)]), False),
        ("M 50,14 C 58,14 64,20 64,27 C 64,36 50,46 50,46 "
         "C 50,46 36,36 36,27 C 36,20 42,14 50,14 Z", True),
    ]


def _highway_exit() -> list[tuple[str, bool]]:
    return [(_polyline([(50, 80), (50, 52), (76, 40)]), False),
            (_arrow_head(80, 37, -25), True)]


def _highway_enter() -> list[tuple[str, bool]]:
    return [(_polyline([(50, 80), (50, 52), (24, 40)]), False),
            (_arrow_head(20, 37, -155), True)]


def _fork_left() -> list[tuple[str, bool]]:
    return [
        (_polyline([(56, 82), (56, 44), (38, 44), (38, 22)]), False),
        (_arrow_head(38, 14, -90), True),
        (_polyline([(56, 44), (74, 44), (74, 22)]), False),
        (_arrow_head(74, 14, -90), True),
    ]


def _fork_right() -> list[tuple[str, bool]]:
    return [
        (_polyline([(44, 82), (44, 44), (62, 44), (62, 22)]), False),
        (_arrow_head(62, 14, -90), True),
        (_polyline([(44, 44), (26, 44), (26, 22)]), False),
        (_arrow_head(26, 14, -90), True),
    ]


def _junction_left() -> list[tuple[str, bool]]:
    return [
        ("M 50,84 L 50,52 M 22,44 L 78,44", False),
        (_polyline([(50, 52), (30, 44), (30, 22)]), False),
        (_arrow_head(30, 14, -90), True),
    ]


def _junction_right() -> list[tuple[str, bool]]:
    return [
        ("M 50,84 L 50,52 M 22,44 L 78,44", False),
        (_polyline([(50, 52), (70, 44), (70, 22)]), False),
        (_arrow_head(70, 14, -90), True),
    ]


def _take_next_left() -> list[tuple[str, bool]]:
    return [(_polyline([(64, 84), (64, 54), (40, 54), (40, 30)]), False),
            (_arrow_head(40, 22, -90), True)]


def _take_next_right() -> list[tuple[str, bool]]:
    return [(_polyline([(36, 84), (36, 54), (60, 54), (60, 30)]), False),
            (_arrow_head(60, 22, -90), True)]


def _left_after_right_curve() -> list[tuple[str, bool]]:
    return [(_polyline([(50, 84), (70, 66), (52, 52), (32, 40), (32, 22)]), False),
            (_arrow_head(32, 14, -90), True)]


def _right_after_left_curve() -> list[tuple[str, bool]]:
    return [(_polyline([(50, 84), (30, 66), (48, 52), (68, 40), (68, 22)]), False),
            (_arrow_head(68, 14, -90), True)]


def _continue_left() -> list[tuple[str, bool]]:
    return [(_polyline([(50, 84), (50, 56)]), False),
            (_polyline([(50, 56), (34, 40), (36, 24)]), False),
            (_arrow_head(36, 16, -80), True)]


def _continue_right() -> list[tuple[str, bool]]:
    return [(_polyline([(50, 84), (50, 56)]), False),
            (_polyline([(50, 56), (66, 40), (64, 24)]), False),
            (_arrow_head(64, 16, -100), True)]


def _ferry_boarding() -> list[tuple[str, bool]]:
    return [
        (_polyline([(24, 74), (76, 74)]), False),
        (_polyline([(34, 74), (46, 60), (58, 70), (70, 56)]), False),
        (_polyline([(70, 56), (62, 56)]), False),
    ]


def _ferry_leaving() -> list[tuple[str, bool]]:
    return [
        (_polyline([(24, 74), (76, 74)]), False),
        (_polyline([(30, 56), (42, 70), (54, 60), (66, 74)]), False),
    ]


def _compass(degrees: float) -> list[tuple[str, bool]]:
    return [
        ("M 50,50 m -34,0 a 34,34 0 1,0 68,0 a 34,34 0 1,0 -68,0", False),
        (_polyline([(50, 50), (50 + 26 * math.cos(math.radians(degrees - 90)),
                                50 + 26 * math.sin(math.radians(degrees - 90)))]), False),
        (_arrow_head(50 + 30 * math.cos(math.radians(degrees - 90)),
                     50 + 30 * math.sin(math.radians(degrees - 90)),
                     degrees - 90, 16.0), True),
    ]


def _roundabout(exit_degrees: int) -> list[tuple[str, bool]]:
    """A roundabout with the exit arrow leaving at the given bearing.

    Bearings are 45-degree steps measured clockwise from straight ahead, which
    is the convention the official table uses and the one that matches India
    driving on the left.
    """
    paths: list[tuple[str, bool]] = [
        ("M 50,50 m -20,0 a 20,20 0 1,0 40,0 a 20,20 0 1,0 -40,0", False),
        (_polyline([(50, 72), (50, 50)]), False),
    ]
    rad = math.radians(exit_degrees - 90)
    tip_x = 50 + 34 * math.cos(rad)
    tip_y = 50 + 34 * math.sin(rad)
    paths.append((_polyline([(tip_x - 12 * math.cos(rad), tip_y - 12 * math.sin(rad)),
                             (tip_x, tip_y)]), False))
    paths.append((_arrow_head(tip_x, tip_y, exit_degrees - 90, 17.0), True))
    return paths


_DRAWERS = {
    "continue_straight": _straight,
    "turn_left": _turn_left,
    "turn_right": _turn_right,
    "slight_left": _slight_left,
    "slight_right": _slight_right,
    "sharp_left_turn": _sharp_left,
    "sharp_right_turn": _sharp_right,
    "make_a_u_turn": _u_turn,
    "destination": _destination,
    "exit_highway": _highway_exit,
    "enter_highway": _highway_enter,
    "left_at_fork": _fork_left,
    "right_at_fork": _fork_right,
    "left_at_junction": _junction_left,
    "right_at_junction": _junction_right,
    "take_next_left": _take_next_left,
    "take_next_right": _take_next_right,
    "take_left_at_junction": _junction_left,
    "take_right_at_junction": _junction_right,
    "sharp_left_at_junciton": _sharp_left,
    "sharp_right_at_junciton": _sharp_right,
    "continue_left": _continue_left,
    "continue_right": _continue_right,
    "left_after_right_curve": _left_after_right_curve,
    "right_after_left_curve": _right_after_left_curve,
    "take_ferry": _ferry_boarding,
    "leave_ferry": _ferry_leaving,
}
for _bearing, _id in ((0, 50), (45, 51), (90, 52), (135, 53), (180, 54), (225, 55), (270, 56), (315, 57)):
    _DRAWERS[f"head_{('north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest')[_bearing // 45]}"] = (
        lambda b=_bearing: _compass(b)
    )
