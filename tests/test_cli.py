"""Tests for the command line.

The CLI is how this tool gets used when there is no browser - pasting a hex
dump from the app's Log tab into `decode` is the fastest way to find out what a
frame meant. So the input parsing matters more than it looks: it has to accept
what the app actually writes, and it has to say what is wrong rather than
reporting "no frames found" on input that is perfectly good.
"""

from __future__ import annotations


import pytest

from ntorq.cli import _extract_frames, _to_bytes, main

#: The exact keepalive the app sent on 2026-10-13, every 2.0 s.
CAPTURED_KEEPALIVE = "5B4A0402000000020000000400000000000000FF"

#: A navigation control frame with distance 550 m written little-endian.
CAPTURED_NAV = "5A4E26029B0126020007010100000000000000FF"

#: `5B 4F "toward Example Rd"`, the captured navigation text frame.
CAPTURED_TEXT = "5B4F" + b"toward Example Rd".hex().upper() + "0000" + "FF"


class TestFrameExtraction:
    def test_a_run_of_hex_digits_is_found(self):
        assert CAPTURED_KEEPALIVE in _extract_frames(CAPTURED_KEEPALIVE)

    def test_space_separated_bytes_are_found(self):
        spaced = "  5B 4A 04 02 00 00 00 02 00 00 00 04 00 00 00 00 00 00 00 FF"
        assert spaced.strip() in _extract_frames(spaced)

    def test_a_run_of_hex_is_found_whole(self):
        """The two patterns must not fight. A single pattern accepting both
        would match the spaced form and stop after two bytes, which produced a
        silent "no frames found" on good input."""
        found = _extract_frames(CAPTURED_KEEPALIVE)
        assert len(found) == 1
        assert len(found[0]) == 40, found[0]

    def test_frames_are_found_among_surrounding_text(self):
        text = (
            "09:50:01.234  out  5B 4A 04 02 00 00 00 02 00 00 00 04 "
            "00 00 00 00 00 00 00 FF\n"
            "some prose between them\n"
            "09:50:03.456  in   5A 4E 26 02 9B 01 26 02 00 07 01 01 "
            "00 00 00 00 00 00 00 FF\n"
        )
        assert len(_extract_frames(text)) == 2

    def test_too_short_a_sequence_is_not_a_frame(self):
        """Four bytes is the floor; two is a number."""
        assert _extract_frames("5B4A") == []
        assert _extract_frames("00 00") == []

    def test_a_run_must_start_with_a_known_start_byte(self):
        """`00000000DEADBEEF` is all hex digits and long enough, and without
        the lead-byte anchor it decoded into a frame nobody sent. Timestamps,
        MAC addresses and standalone checksums all look like this."""
        assert _extract_frames("at 00000000DEADBEEF exactly") == []
        assert _extract_frames("9C4A0402000000020000000400000000000000FF") == []

    def test_every_documented_start_byte_is_accepted(self):
        """`0x5A` control, `0x5B` text and keepalive, `0x9A`/`0x9B` handshake."""
        for lead in ("5A", "5B", "9A", "9B"):
            frame = lead + "4E260200000000000000000000000000000000FF"
            assert frame in _extract_frames(frame), lead

    def test_several_frames_are_returned_in_order(self):
        text = f"{CAPTURED_KEEPALIVE}\n{CAPTURED_NAV}\n{CAPTURED_TEXT}"
        found = _extract_frames(text)
        assert len(found) == 3
        assert found[0].startswith("5B4A")
        assert found[1].startswith("5A4E")
        assert found[2].startswith("5B4F")




class TestToBytes:
    def test_a_spaced_frame_converts(self):
        assert _to_bytes("5B 4A 04") == bytes([0x5B, 0x4A, 0x04])

    def test_a_run_converts_to_separate_bytes(self):
        """Not one enormous integer: `int("5B4A04", 16)` is 5,983,748 and
        `bytes()` rejects it as out of range."""
        assert _to_bytes("5B4A04") == bytes([0x5B, 0x4A, 0x04])

    def test_an_odd_number_of_digits_is_rejected_with_a_reason(self):
        with pytest.raises(ValueError) as excinfo:
            _to_bytes("5B4A0")
        assert "whole number of bytes" in str(excinfo.value)


class TestDecodeCommand:
    def _run(self, stdin: str, capsys) -> tuple[int, str]:
        import io
        import sys

        saved = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            code = main(["decode", "-"])
        finally:
            sys.stdin = saved
        return code, capsys.readouterr().out + capsys.readouterr().err

    def test_the_captured_keepalive_decodes(self, capsys):
        code, output = self._run(CAPTURED_KEEPALIVE + "\n", capsys)
        assert code == 0
        assert "mobile-data" in output
        assert "signal 4, battery 2" in output

    def test_the_captured_nav_frame_reports_both_byte_orders(self, capsys):
        code, output = self._run(CAPTURED_NAV + "\n", capsys)
        assert code == 0
        assert "550 m" in output
        assert "9730 m" in output
        assert "not established on hardware" in output

    def test_the_captured_text_frame_decodes_its_text(self, capsys):
        code, output = self._run(CAPTURED_TEXT + "\n", capsys)
        assert code == 0
        assert "toward Example Rd" in output
        assert "17 chars" in output

    def test_a_log_tab_excerpt_decodes(self, capsys):
        text = (
            "  12345.678  out  5A 10 00 02 88 2A 02 00 00 00 09 50 00 "
            "00 00 00 00 00 00 00 FF\n"
            "  12347.680  in   5B 4A 04 02 00 00 00 02 00 00 00 04 00 "
            "00 00 00 00 00 00 00 FF\n"
        )
        code, output = self._run(text, capsys)
        assert code == 0
        assert output.count("kind:") == 2

    def test_no_frames_says_what_was_expected(self, capsys):
        import io
        import sys

        saved = sys.stdin
        sys.stdin = io.StringIO("nothing hexadecimal here\n")
        try:
            code = main(["decode", "-"])
        finally:
            sys.stdin = saved
        captured = capsys.readouterr()
        assert code == 1
        assert "5B 4A 04" in captured.err, "the error should show a real frame"

    def test_an_odd_digit_count_does_not_crash(self, capsys):
        """A truncated paste from a terminal is the likely real-world case."""
        code, _ = self._run("5B4A0402000000020000000400000000000000F\n", capsys)
        assert code == 0 or code == 1  # either is fine; a traceback is not


class TestRenderCommand:
    def test_render_writes_an_svg(self, tmp_path, capsys):
        out = tmp_path / "dash.svg"
        code = main(["render", "-o", str(out)])
        assert code == 0
        assert out.read_text().startswith("<svg")

    def test_render_to_stdout(self, capsys):
        assert main(["render", "-o", "-"]) == 0
        assert "<svg" in capsys.readouterr().out

    def test_render_accepts_a_scenario(self, tmp_path):
        out = tmp_path / "dash.svg"
        assert main(["render", "--scenario", "overheating", "-o", str(out)]) == 0
        svg = out.read_text()
        assert "ENGINE" in svg and "WARNING" in svg

    def test_render_rejects_an_unknown_scenario(self, tmp_path, capsys):
        code = main(["render", "--scenario", "apollo", "-o", str(tmp_path / "x.svg")])
        assert code == 2
        assert "cold-start" in capsys.readouterr().err

    def test_render_flags_both_overlays_together(self, tmp_path):
        out = tmp_path / "dash.svg"
        assert main(["render", "--all-segments", "--overlay", "-o", str(out)]) == 0
        assert "line 1 = 10 cells; line 2 = 12 cells" in out.read_text()


class TestFramesCommand:
    def test_frames_summarises_what_was_sent(self, capsys):
        assert main(["frames", "--seconds", "20"]) == 0
        output = capsys.readouterr().out
        assert "5A 10" in output
        assert "5A 16" in output
        assert "frames in" in output

    def test_frame_19_is_absent_by_default(self, capsys):
        """This cluster never sent it, so a default stream containing it would
        be training the app on a frame the vehicle does not produce."""
        assert main(["frames", "--seconds", "30"]) == 0
        assert "5A 19" not in capsys.readouterr().out

    def test_the_startup_zero_frames_are_counted_separately(self, capsys):
        """They have no data id worth reporting, so they show as `00 00`."""
        assert main(["frames", "--seconds", "10"]) == 0
        assert "00 00" in capsys.readouterr().out


class TestHelp:
    @pytest.mark.parametrize(
        "command", ["serve", "ble", "frames", "render", "decode"]
    )
    def test_every_subcommand_has_help(self, command, capsys):
        with pytest.raises(SystemExit):
            main([command, "--help"])
        assert capsys.readouterr().out

    def test_no_command_prints_usage(self, capsys):
        assert main([]) == 1
        assert "ntorq-console" in capsys.readouterr().out
