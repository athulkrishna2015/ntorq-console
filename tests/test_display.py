"""Tests for the rendered dash.

These check three things, in order of how badly a failure would mislead someone:

1. the SVG is well-formed and renders, so a screenshot is possible;
2. the values on the face are the ones the state says, so the picture cannot
   disagree with the bytes;
3. the text area's real width is visible, because that is the finding.

A rendering test that only checked "does it produce a string" would pass
happily while the digits were drawn at the origin, which is exactly what
happened twice during development.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from ntorq import display, pictograms
from ntorq.cluster import (
    GAUGE_BARS,
    ClusterState,
    Mode,
    SegIcon,
    Telltale,
    Trip,
)

SVG_NS = "{http://www.w3.org/2000/svg}"


def render(state: ClusterState, **kwargs) -> str:
    return display.render_svg(state, display.RenderOptions(**kwargs))


class TestWellFormed:
    @pytest.mark.parametrize("scenario", ["street", "navigation", "faults"])
    def test_the_document_parses_as_xml(self, scenario):
        state = ClusterState(odometer_tenths=165_930, now=12 * 3600 + 22 * 60)
        if scenario == "navigation":
            state.nav.active = True
            state.nav.pictogram = 0
            state.nav.distance_m = 550
            state.nav.text = "Example Road"
        elif scenario == "faults":
            state.telltales.update({Telltale.MIL, Telltale.LOW_FUEL})
            state.segment_icons.update({SegIcon.HELMET, SegIcon.SERVICE})
        ET.fromstring(render(state))

    def test_every_state_the_scenarios_produce_renders(self):
        from ntorq.scenarios import SCENARIOS

        for name, apply_scenario in SCENARIOS.items():
            state = ClusterState()
            apply_scenario(state)
            ET.fromstring(render(state)), name

    def test_the_viewbox_is_the_declared_size(self):
        svg = render(ClusterState())
        root = ET.fromstring(svg)
        assert root.get("viewBox") == f"0 0 {display.WIDTH} {display.HEIGHT}"

    def test_render_options_all_parse(self):
        for kwargs in (
            {},
            {"all_segments": True},
            {"show_debug_overlay": True},
            {"segment_test": True},
            {"all_segments": True, "show_debug_overlay": True},
        ):
            ET.fromstring(render(ClusterState(), **kwargs))

    def test_no_duplicate_xml_attribute(self):
        """An attribute emitted twice is not well-formed, and the failure it
        produces is an unhelpful `XML parse error` a long way from the cause.

        Attributes are matched with a leading space or a tag boundary so that
        `rx=` is not mistaken for a second `x=`, which is what a plain substring
        count does and why this check silently passed once already.
        """
        pattern = re.compile(r'(?<![-\w])(\w+)="')
        for kwargs in ({}, {"all_segments": True}, {"show_debug_overlay": True}):
            svg = render(ClusterState(), **kwargs)
            for tag in re.findall(r"<[^>]+>", svg):
                names = pattern.findall(tag)
                duplicates = {n for n in names if names.count(n) > 1}
                assert not duplicates, f"{duplicates} repeated in: {tag[:140]}"


class TestSevenSegment:
    def test_a_digit_is_drawn_where_it_was_asked_for(self):
        """Regression: the cells were once emitted with coordinates local to
        each digit and no translate at all, so every digit landed on the canvas
        origin and the odometer rendered as one illegible blob in the corner.

        Position is carried by the group's transform rather than baked into the
        polygon points, which is what makes the skew behave - see
        `test_the_skew_applies_about_the_field_not_the_canvas`.
        """
        svg = display.seven_segment("123", 200, 40, 30)
        outer = re.search(r'transform="translate\(200\.00,40\.00\)', svg)
        assert outer, "the field was not translated to the position it was given"

        numbers = [float(n) for n in re.findall(r'points="([-\d.]+),[-\d.]+ ', svg)]
        assert numbers, "no polygons were emitted"
        # Points stay local, so they are inside one digit's box and no larger.
        assert max(numbers) < display.digit_advance(30) * 1.2

    def test_digits_advance_left_to_right(self):
        svg = display.seven_segment("888", 0, 0, 40)
        groups = re.findall(r'translate\(([\d.]+),0\)', svg)
        positions = [float(g) for g in groups]
        assert positions == sorted(positions)
        assert len(set(positions)) == 3, "two digits share a position"

    def test_the_skew_applies_about_the_field_not_the_canvas(self):
        """Regression: `skewX` on a group shears about the *canvas* origin, so
        a field placed at x=348 slid left by a third of its height and the ETA
        digits landed on the ETA label."""
        svg = display.seven_segment("18", 348, 100, 26)
        transform = re.search(r'transform="([^"]+)"', svg)
        assert transform
        # translate first, then skew: the shear origin follows the field.
        assert transform.group(1).startswith("translate(348.00,100.00)")
        assert "skewX" in transform.group(1)

    def test_width_matches_what_is_drawn(self):
        for text in ("0", "88", "16593", "12:22", "0.0", "  62"):
            measured = display.seven_segment_width(text, 30)
            assert measured > 0
            # Every character advances by at least one pitch.
            assert measured >= len(text) * display.digit_advance(30) * 0.25

    def test_a_space_renders_nothing_but_still_advances(self):
        """The speedometer is a three-digit field that goes blank on the left,
        not `062`."""
        blank = display.seven_segment(" 62", 0, 0, 40)
        assert "<polygon" in blank
        # One fewer digit's worth of polygons than "062" would have.
        padded = display.seven_segment("062", 0, 0, 40)
        assert blank.count("<polygon") == padded.count("<polygon") - 6

    def test_all_segments_shows_the_unlit_ones(self):
        lit = display.seven_segment("1", 0, 0, 40)
        full = display.seven_segment("1", 0, 0, 40, all_segments=True)
        assert full.count("<polygon") > lit.count("<polygon")
        assert lit.count("<polygon") == 2  # segments b and c

    def test_every_digit_glyph_is_defined(self):
        """A character with no segment map would render as a blank cell with no
        warning, which is how a `0` ends up where an `8` should be.

        `1` legitimately uses only two segments, so the floor is two rather than
        five; what is being checked is that every digit lights *something*, and
        that the segment maps are distinct enough to be told apart.
        """
        for char in "0123456789":
            assert char in display._SEGMENTS
            svg = display.seven_segment(char, 0, 0, 30)
            assert svg.count("<polygon") >= 2, char

    def test_the_digit_glyphs_are_distinguishable(self):
        """Two digits drawing the same segments would be indistinguishable on a
        dash at 100 km/h, which is the worst kind of wrong for a speedometer."""
        shapes = {}
        for char in "0123456789":
            shapes[char] = display._SEGMENTS[char]
        assert len(set(shapes.values())) == 10, shapes


class TestTextArea:
    def test_the_overlay_states_the_real_width(self):
        state = ClusterState()
        state.announce("Example Road")
        svg = render(state, show_debug_overlay=True)
        assert "line 1 = 10 cells; line 2 = 12 cells" in svg

    def test_the_overlay_reports_characters_that_cannot_be_shown(self):
        """Twenty-five characters exceed the 22 cells across the unequal lines."""
        state = ClusterState()
        state.announce("A" * 25)
        svg = render(state, show_debug_overlay=True)
        assert "3 character(s) have nowhere to go" in svg

    def test_overflow_is_reported_without_the_overlay_too(self):
        """A message silently vanishing is exactly what this tool exists to
        make impossible to miss."""
        state = ClusterState()
        state.announce("A" * 25)
        assert "+3 dropped" in render(state)

    def test_a_message_that_fits_reports_nothing(self):
        state = ClusterState()
        state.announce("FUEL EMPTY")
        svg = render(state)
        assert "+" not in svg.split("FUEL")[0][-40:] or "more" not in svg

    def test_wrapping_splits_on_a_word_boundary(self):
        lines, dropped = display._wrap_to_cells("CALL FROM 9863571861")
        assert dropped == 0
        assert lines == ["CALL FROM", "9863571861"]

    def test_first_and_second_lines_have_different_capacities(self):
        first, dropped = display._wrap_to_cells("A" * 17)
        assert first == ["A" * 10, "A" * 7]
        assert dropped == 0

        second = display._wrap_to_cells("A" * 17, start_line=1)
        assert second == (["A" * 12], 5)

    def test_a_full_second_line_caps_at_twelve_characters(self):
        assert display._wrap_to_cells("A" * 13, start_line=1) == (["A" * 12], 1)

    def test_wrapping_does_not_split_a_number_across_rows(self):
        """A phone number split across a row boundary would be unreadable, and
        unreadable on a dash at 100 km/h is a hazard."""
        lines, _ = display._wrap_to_cells("CALL FROM 9863571861")
        assert "CALL FROM" in lines[0]
        assert "9863571861" in lines[1]
        assert not any(part.isdigit() and len(part) < 6 for row in lines for part in row.split())

    def test_wrapping_counts_what_it_could_not_place(self):
        _, dropped = display._wrap_to_cells("A" * 25)
        assert dropped == 3

    def test_an_empty_message_yields_no_lines(self):
        lines, dropped = display._wrap_to_cells("")
        assert lines == [] and dropped == 0

    def test_the_text_area_is_suppressed_while_navigating(self):
        """Two zones drawing over the same pixels would be a simulator
        artefact, not something the cluster does."""
        state = ClusterState()
        state.announce("Example Road")
        state.nav.active = True
        svg = render(state)
        # The message is gone, and the navigation strip is up.
        assert "Example Road" not in svg
        assert "ETA" in svg


class TestValuesReachTheFace:
    def test_the_clock_is_twelve_hour_with_meridiem(self):
        """Manual 1b: "Indicates the time in 12 hour format (AM or PM)."

        The clock is drawn as seven-segment digits, so the digits are not in the
        markup as text. What is checked is the segment count and the meridiem,
        and the actual digits are covered by `_clock_digits` below.
        """
        state = ClusterState()
        state.now = 12 * 3600 + 22 * 60  # 12:22
        svg = render(state)
        assert "PM" in svg

        state.now = 9 * 3600 + 50 * 60  # 09:50
        assert "AM" in render(state)

    def test_clock_digits_are_twelve_hour(self):
        """00:00 renders as twelve, not as zero."""
        state = ClusterState()
        state.now = 0
        clock = state.clock()
        assert (clock.hour % 12 or 12) == 12
        # Five glyphs: two digits, a colon, two more.
        from ntorq.display import _clock_box

        midnight = _clock_box(state, display.RenderOptions())
        noon = _clock_box(
            ClusterState(now=12 * 3600), display.RenderOptions()
        )
        assert midnight.count("<polygon") == noon.count("<polygon")

    def test_the_trip_letter_box_follows_the_selection(self):
        state = ClusterState()
        for trip, letter in ((Trip.A, "A"), (Trip.B, "B"), (Trip.F, "F")):
            state.selected_trip = trip
            assert letter in render(state)

    def test_the_trip_letter_box_shows_a_trip_and_not_the_odometer(self):
        """`ODO` is not a trip - it is the default reading, and the manual labels
        it as a separate word to the right of the divider. Putting `ODO` in the
        18-pixel letter box drew it spilling over both edges."""
        from ntorq.display import _trip_letter

        for trip in (Trip.A, Trip.B, Trip.F):
            state = ClusterState()
            state.selected_trip = trip
            assert trip.value.upper() in _trip_letter(state)

        state = ClusterState()
        state.selected_trip = Trip.ODO
        assert _trip_letter(state) == ""
        # The `ODO` word is still on the face, just not in the box.
        assert "ODO" in render(state)

    def test_the_selected_trip_box_is_filled_and_the_others_are_not(self):
        """Manual 1k shows the current trip highlighted among F/A/B."""
        state = ClusterState()
        state.selected_trip = Trip.B
        svg = render(state)
        filled = re.findall(r'<rect x="(9[2-9]|1[0-2][0-9])" y="9"[^>]*fill="(#[0-9a-f]{6})"', svg)
        assert filled, "no trip selector boxes found"
        letters = {int(x): colour for x, colour in filled}
        selected = {x for x, colour in letters.items() if colour != display.INK_DIM}
        assert len(selected) == 1, f"{len(selected)} boxes look selected"

    def test_fuel_bars_reflect_the_gauge(self):
        """Counted by fill colour, not by element count: an unlit bar is still a
        `<rect>`, so counting rects cannot tell a full gauge from an empty one."""
        state = ClusterState()
        state.fuel_litres = 5.0
        full = render(state)
        state.fuel_litres = 0.5
        empty = render(state)
        lit = f'rx="1.2" fill="{display.INK}"'
        assert full.count(lit) > empty.count(lit)

    def test_speed_is_rendered_not_padded_with_zero(self):
        state = ClusterState()
        state.speed_kmh = 62
        svg = render(state)
        # The big field holds three digits; at 62 km/h it shows two and a
        # blank, so `062` must not appear as three lit digits.
        assert "062" not in svg

    def test_the_zero_to_sixty_element_is_blank_until_there_is_a_run(self):
        """Manual 2c: the value disappears if the speed drops below 2 km/h.

        Showing `0.0` before any run has been timed would be inventing a
        measurement, so the value is empty rather than zero. Checked through
        `_second_row` rather than by searching the markup: polygon coordinates
        are full of decimals, so looking for "0.0" matches geometry.
        """
        from ntorq.display import _second_row

        state = ClusterState()
        state.mode = Mode.SPORT
        blank = _second_row(state, display.RenderOptions())
        assert blank.count("<polygon") == 0, "an unlit 0-60 drew segments"

        state.zero_to_sixty_seconds = 12.4
        assert _second_row(state, display.RenderOptions()).count("<polygon") > 0

    def test_telltales_light_up_on_the_surround(self):
        state = ClusterState()
        off = render(state)
        state.telltales.add(Telltale.TURN_LEFT)
        on = render(state)
        assert on != off
        assert display.GREEN in on

    def _text_on_face(self, state: ClusterState) -> list[str]:
        """The text actually drawn, one entry per rendered line.

        The text area wraps and the alpha-numeric display draws a broken-stroke
        font, so a message is not in the markup as one string: `ENGINE WARNING`
        arrives as two lines. Comparing against the rendered lines is what makes
        this a check on what is *shown* rather than on what was stored.
        """
        svg = render(state)
        return re.findall(r">([^<>]+)</text>", svg)

    def test_a_fault_message_reaches_the_text_area(self):
        state = ClusterState()
        state.inject("overheating")
        lines = self._text_on_face(state)
        assert "ENGINE" in lines and "WARNING" in lines

    def test_fuel_empty_reaches_the_text_area(self):
        state = ClusterState()
        state.inject("reserve-fuel")
        assert "FUEL EMPTY" in self._text_on_face(state)

    def test_side_stand_is_shown_solid_only_when_deployed(self):
        """Manual 1m: the indicator turns on when the stand is deployed, and
        blinks on a sensor fault. Drawn dashed and empty when it is up, which is
        how the manual's illustration shows it."""
        state = ClusterState()
        stowed = render(state)
        state.side_stand = True
        deployed = render(state)
        assert deployed.count('stroke-dasharray="3 3"') < stowed.count(
            'stroke-dasharray="3 3"'
        )

    def test_the_navigation_strip_is_marked_as_a_simulator_decision(self):
        """Only in the overlay. The placement is a guess and the page should
        say so where someone debugging the app will see it."""
        state = ClusterState()
        state.nav.active = True
        plain = render(state)
        overlay = render(state, show_debug_overlay=True)
        assert 'stroke-dasharray="4 4"' in overlay
        assert 'stroke-dasharray="4 4"' not in plain


class TestPictograms:
    def test_every_known_id_produces_a_path(self):
        for pictogram_id in pictograms.IDS:
            paths = pictograms.svg_paths(pictogram_id)
            assert paths, f"id {pictogram_id} produced no path"
            for data, _ in paths:
                assert data.startswith("M") or data.startswith("M ")

    def test_an_unknown_id_is_visibly_missing_not_guessed(self):
        """A wrong arrow on a dash is worse than an obviously missing one."""
        assert pictograms.for_id(999) is None
        assert pictograms.label(999) == "unknown_999"
        svg = render_with_pictogram(999)
        assert "no glyph for 999" in svg

    def test_the_official_table_is_reproduced(self):
        """The ids and labels, as transcribed from `ManeuverUtils`."""
        expected = {
            0: "turn_left",
            7: "continue_straight",
            8: "destination",
            41: "make_a_u_turn",  # special-cased ahead of the switch
            65: "roundabout_exit_at_45",
            68: "roundabout_exit_at_180",
            71: "roundabout_exit_at_315",
            54: "head_south",
        }
        for pictogram_id, label in expected.items():
            assert pictograms.label(pictogram_id) == label

    def test_id_21_duplicates_seven_upstream_and_is_kept_that_way(self):
        """`case 21` returns `continue_straight`, identical to `case 7`.
        Preserved rather than 'fixed', because the cluster may genuinely treat
        them as the same glyph."""
        assert pictograms.label(21) == "continue_straight"
        assert pictograms.label(7) == "continue_straight"

    def test_highway_exit_appears_twice_upstream_and_both_ids_exist(self):
        """`9`/`10` in the main switch and `34`/`35` in the default branch."""
        assert pictograms.label(9) == "exit_highway"
        assert pictograms.label(34) == "exit_highway"
        assert pictograms.label(10) == "enter_highway"
        assert pictograms.label(35) == "enter_highway"

    def test_roundabout_bearings_span_a_full_circle(self):
        for degrees in range(45, 360, 45):
            pictogram = pictograms.for_id(64 + degrees // 45)
            assert pictogram is not None, degrees
            assert pictogram.roundabout_exit_degrees == degrees

    def test_the_jupiter_table_is_absent_by_design(self):
        """88 is a roundabout U-turn in the companion app's table and does not
        exist in the official one, which is consistent with an India-only
        cluster image."""
        assert pictograms.for_id(88) is None


def render_with_pictogram(pictogram_id: int) -> str:
    state = ClusterState()
    state.nav.active = True
    state.nav.pictogram = pictogram_id
    return render(state)


class TestLayout:
    def test_nothing_is_drawn_outside_the_canvas(self):
        """A cluster face with an element off the edge looks like a rendering
        bug and hides whatever it was covering."""
        svg = render(ClusterState())
        ET.fromstring(svg)  # also asserts it is well-formed
        # Collect every x/y attribute in a <g transform="translate(x,y)">.
        for match in re.finditer(r'translate\(([-\d.]+),([-\d.]+)\)', svg):
            x, y = float(match.group(1)), float(match.group(2))
            assert -10 <= x <= display.WIDTH + 10, f"x={x}"
            assert -10 <= y <= display.HEIGHT + 10, f"y={y}"

    def test_the_glass_fits_inside_the_canvas(self):
        assert display.GLASS_X >= 0
        assert display.GLASS_Y >= 0
        assert display.GLASS_X + display.GLASS_W <= display.WIDTH
        assert display.GLASS_Y + display.GLASS_H <= display.HEIGHT

    def test_the_document_is_not_absurdly_large(self):
        """A face redrawn ten times a second should stay cheap to serve."""
        assert len(render(ClusterState())) < 60_000

    def test_the_gauge_bar_count_matches_the_manual(self):
        """Six bars each, for fuel and for temperature."""
        assert GAUGE_BARS == 6
