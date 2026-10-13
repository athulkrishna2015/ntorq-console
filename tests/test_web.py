"""Tests for the web display: the control surface and the HTTP layer.

The commands are the interesting part. Each one is something an operator does
while debugging the app, and each one has a way to silently do nothing - a
slider that moves but changes no state, a scenario that sets fields the display
does not read. So each test asserts that the *observable* result changed, not
merely that the call returned.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from ntorq.cluster import ClusterState, Mode, SegIcon, Telltale, Trip
from ntorq.sim import SimulatedCluster
from ntorq.web.app import ConsoleApp, ConsoleServer


@pytest.fixture
def app() -> ConsoleApp:
    cluster = SimulatedCluster(state=ClusterState(odometer_tenths=165_930))
    return ConsoleApp(cluster)


@pytest.fixture
def server(app: ConsoleApp):
    """A server bound to the [app] fixture.

    The app is passed in rather than assigned afterwards: a handler that captured
    the app at construction would keep serving the original cluster, so every
    HTTP test would pass while changing nothing.
    """
    httpd = ConsoleServer(app.cluster, host="127.0.0.1", port=0, app=app)
    yield httpd
    httpd.stop()


class TestCommands:
    def test_every_command_name_resolves(self, app: ConsoleApp):
        """A typo in the page or a rename would otherwise be a 500 at the
        moment someone is trying to reproduce a fault."""
        for name in app.command_names():
            # Faults and modes take arguments, so only check that they resolve
            # to a callable rather than calling them all with nothing.
            assert callable(getattr(app, f"_cmd_{name}"))

    def test_an_unknown_command_says_what_is_available(self, app: ConsoleApp):
        with pytest.raises(ValueError) as excinfo:
            app.command("no-such-command", {})
        assert "no control named" in str(excinfo.value)
        assert "speed" in str(excinfo.value)

    def test_speed_changes_what_the_display_would_show(self, app: ConsoleApp):
        app.command("speed", {"value": 64})
        assert app.cluster.state.speed_kmh == 64
        assert app.snapshot()["speedKmh"] == 64

    def test_fuel_is_clamped_to_the_tank(self, app: ConsoleApp):
        app.command("fuel", {"litres": 99})
        assert app.cluster.state.fuel_litres == pytest.approx(5.0)
        app.command("fuel", {"litres": -5})
        assert app.cluster.state.fuel_litres >= 0

    def test_the_fuel_byte_can_be_set_directly(self, app: ConsoleApp):
        """The wire value and the gauge are different things, and driving them
        separately is how you look at what the app renders for a given byte."""
        app.command("fuel-bars", {"bars": 4})
        assert app.cluster.state.raw_fuel_override == 4

    def test_a_fuel_byte_the_gauge_cannot_express_is_still_allowed(
        self, app: ConsoleApp
    ):
        """Seven is the value the rider's cluster actually sent in all 772
        captured frames while its gauge read FUEL EMPTY. Refusing to let the
        simulator send it would make the most interesting case the one that
        cannot be reproduced, and it is flagged as unrenderable instead."""
        result = app.command("fuel-bars", {"bars": 7})
        assert app.cluster.state.raw_fuel_override == 7
        assert result["renderable"] is False
        assert "six-bar gauge" in result["note"]

    def test_a_fuel_byte_within_the_gauge_is_not_flagged(self, app: ConsoleApp):
        result = app.command("fuel-bars", {"bars": 6})
        assert result["renderable"] is True
        assert "note" not in result

    def test_a_fuel_byte_beyond_the_nibble_is_rejected_with_a_reason(
        self, app: ConsoleApp
    ):
        with pytest.raises(ValueError) as excinfo:
            app.command("fuel-bars", {"bars": 16})
        assert "low nibble" in str(excinfo.value)

    def test_clearing_the_fuel_byte_returns_to_the_litre_mapping(
        self, app: ConsoleApp
    ):
        app.command("fuel-bars", {"bars": 7})
        app.command("raw-fuel-clear", {})
        assert app.cluster.state.raw_fuel_override is None

    def test_temperature_is_clamped(self, app: ConsoleApp):
        app.command("temp", {"celsius": 500})
        assert app.cluster.state.engine_temp_c <= 120

    def test_mode_is_validated_and_applied(self, app: ConsoleApp):
        app.command("mode", {"mode": "sport"})
        assert app.cluster.state.mode == Mode.SPORT

    def test_an_unknown_mode_lists_the_ones_that_exist(self, app: ConsoleApp):
        with pytest.raises(ValueError) as excinfo:
            app.command("mode", {"mode": "turbo"})
        assert "street" in str(excinfo.value)

    def test_trip_is_validated_and_applied(self, app: ConsoleApp):
        app.command("trip", {"trip": "b"})
        assert app.cluster.state.selected_trip == Trip.B

    def test_ignition_off_clears_the_display(self, app: ConsoleApp):
        """Manual: with the ignition off the cluster keeps the odometer and the
        clock and drops everything else."""
        app.cluster.connect()
        app.command("ignition", {"on": False})
        assert app.cluster.state.ignition is False
        assert app.cluster.state.message is None
        assert not app.cluster.state.segment_icons

    def test_a_switch_toggles_the_named_behaviour(self, app: ConsoleApp):
        app.command("switch", {"name": "frame19", "value": True})
        assert app.cluster.state.emit_frame_19 is True
        app.command("switch", {"name": "frame19", "value": False})
        assert app.cluster.state.emit_frame_19 is False

    def test_an_unknown_switch_lists_the_ones_that_exist(self, app: ConsoleApp):
        with pytest.raises(ValueError) as excinfo:
            app.command("switch", {"name": "warp", "value": True})
        assert "frame19" in str(excinfo.value)

    def test_a_display_switch_changes_the_render_options(self, app: ConsoleApp):
        app.command("switch", {"name": "debug-overlay", "value": True})
        assert app.render_options.show_debug_overlay is True
        app.command("switch", {"name": "all-segments", "value": True})
        assert app.render_options.all_segments is True

    def test_a_fault_is_applied_and_reported(self, app: ConsoleApp):
        result = app.command("fault", {"name": "mil"})
        assert result["fault"] == "mil"
        assert Telltale.MIL in app.cluster.state.telltales

    def test_clearing_faults_removes_them(self, app: ConsoleApp):
        app.command("fault", {"name": "mil"})
        app.command("fault", {"name": "clear"})
        assert not app.cluster.state.telltales

    def test_an_unknown_fault_lists_the_ones_that_exist(self, app: ConsoleApp):
        with pytest.raises(ValueError) as excinfo:
            app.command("fault", {"name": "flat-tyre"})
        assert "low-fuel" in str(excinfo.value)

    def test_connect_and_disconnect_raise_the_documented_messages(
        self, app: ConsoleApp
    ):
        app.command("connect", {"name": "EXAMPLE"})
        assert app.cluster.state.message.text == "CONNECTION SUCCESSFUL"
        app.command("disconnect", {})
        assert app.cluster.state.message.text == "EXAMPLE DISCONNECTED"

    def test_a_call_raises_the_manuals_message_and_lights_the_led(
        self, app: ConsoleApp
    ):
        """Manual: "CALL FROM AMIT" and the green LED blinks until the call is
        active."""
        app.command("call", {"name": "AMIT"})
        assert app.cluster.state.message.text == "CALL FROM AMIT"
        assert Telltale.NOTIFY in app.cluster.state.telltales

    def test_a_missed_call_counts(self, app: ConsoleApp):
        app.command("call", {"name": "AMIT"})
        app.command("hangup", {"missed": True})
        assert app.cluster.state.missed_calls == 1

    def test_an_sms_raises_the_message_and_the_envelope(self, app: ConsoleApp):
        app.command("sms", {"name": "AMIT"})
        assert app.cluster.state.message.text == "SMS FROM AMIT"
        assert SegIcon.ENVELOPE in app.cluster.state.segment_icons

    def test_a_telltale_toggles(self, app: ConsoleApp):
        app.command("telltale", {"name": "turn-left", "on": True})
        assert Telltale.TURN_LEFT in app.cluster.state.telltales
        app.command("telltale", {"name": "turn-left", "on": False})
        assert Telltale.TURN_LEFT not in app.cluster.state.telltales

    def test_an_unknown_telltale_lists_the_ones_that_exist(self, app: ConsoleApp):
        with pytest.raises(ValueError) as excinfo:
            app.command("telltale", {"name": "under-inflation"})
        assert "eco" in str(excinfo.value)

    def test_a_scenario_is_applied(self, app: ConsoleApp):
        app.command("scenario", {"name": "overheating"})
        assert app.cluster.state.temp_over_range()

    def test_an_unknown_scenario_lists_the_ones_that_exist(self, app: ConsoleApp):
        with pytest.raises(ValueError) as excinfo:
            app.command("scenario", {"name": "apollo"})
        assert "cold-start" in str(excinfo.value)

    def test_reset_clears_the_vehicle_state(self, app: ConsoleApp):
        app.command("fault", {"name": "mil"})
        app.command("connect", {})
        app.command("reset", {})
        assert not app.cluster.state.telltales
        assert app.cluster.connected is False

    def test_reset_keeps_the_operator_switches(self, app: ConsoleApp):
        """The switches are the operator's settings, not vehicle state, and
        losing them on a reset would be maddening mid-debug."""
        app.command("switch", {"name": "frame19", "value": True})
        app.command("reset", {})
        assert app.cluster.state.emit_frame_19 is True


class TestRendering:
    def test_the_svg_renders_and_declares_its_size(self, app: ConsoleApp):
        svg = app.svg()
        assert svg.startswith("<svg")
        assert 'viewBox="0 0 1000 620"' in svg

    def test_a_command_that_changes_state_changes_the_svg(self, app: ConsoleApp):
        before = app.svg()
        app.command("speed", {"value": 88})
        assert app.svg() != before

    def test_the_overlay_reaches_the_rendered_output(self, app: ConsoleApp):
        app.command("switch", {"name": "debug-overlay", "value": True})
        assert "10 cells x 2 rows" in app.svg()

    def test_the_snapshot_is_json_serialisable(self, app: ConsoleApp):
        json.dumps(app.snapshot())

    def test_the_snapshot_advertises_the_scenarios_and_gatt_status(
        self, app: ConsoleApp
    ):
        snapshot = app.snapshot()
        assert "cold-start" in snapshot["scenarios"]
        assert "gatt" in snapshot
        assert "render" in snapshot


class TestHttp:
    def _get(self, server: ConsoleServer, path: str):
        host, port = server.address[0], server.address[1]
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=5) as response:
            return response.status, response.read()

    def _post(self, server: ConsoleServer, body: dict):
        host, port = server.address[0], server.address[1]
        request = urllib.request.Request(
            f"http://{host}:{port}/api/command",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    @pytest.fixture(autouse=True)
    def _running(self, server: ConsoleServer):
        server.start_background()
        yield
        server.stop()

    def test_the_index_page_is_served(self, server: ConsoleServer):
        status, body = self._get(server, "/")
        assert status == 200
        assert b"Ntorq console" in body

    def test_the_dash_is_served_as_svg(self, server: ConsoleServer):
        status, body = self._get(server, "/dash.svg")
        assert status == 200
        assert body.startswith(b"<svg")

    def test_the_state_endpoint_returns_json(self, server: ConsoleServer):
        status, body = self._get(server, "/api/state")
        assert status == 200
        snapshot = json.loads(body)
        assert "frames" in snapshot

    def test_a_command_over_http_works(self, server: ConsoleServer):
        status, payload = self._post(
            server, {"command": "speed", "params": {"value": 42}}
        )
        assert status == 200
        assert payload["ok"]
        assert server.app.cluster.state.speed_kmh == 42

    def test_an_unknown_command_is_a_400_with_a_reason(self, server: ConsoleServer):
        status, payload = self._post(server, {"command": "warp"})
        assert status == 400
        assert "no control named" in payload["error"]

    def test_a_malformed_body_is_a_400_not_a_crash(self, server: ConsoleServer):
        host, port = server.address[0], server.address[1]
        request = urllib.request.Request(
            f"http://{host}:{port}/api/command",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5):
                pytest.fail("a malformed body was accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 400
            assert "not JSON" in json.loads(error.read())["error"]

    def test_a_get_to_the_command_endpoint_explains_itself(self, server: ConsoleServer):
        try:
            self._get(server, "/api/command")
            pytest.fail("GET on the command endpoint was accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 405
            assert "POST" in json.loads(error.read())["error"]

    def test_an_unknown_route_is_a_404(self, server: ConsoleServer):
        try:
            self._get(server, "/nope")
            pytest.fail("an unknown route was served")
        except urllib.error.HTTPError as error:
            assert error.code == 404

    def test_static_files_are_served(self, server: ConsoleServer):
        status, body = self._get(server, "/static/app.js")
        assert status == 200
        assert b"console" in body or b"poll" in body

    def test_a_path_traversal_is_refused(self, server: ConsoleServer):
        """The static handler resolves inside its directory and checks. A
        benchmark tool should not be the thing that reads /etc/passwd."""
        for attempt in ("/static/../../../../etc/passwd", "/static/../app.py"):
            try:
                self._get(server, attempt)
                pytest.fail(f"{attempt} was served")
            except urllib.error.HTTPError as error:
                assert error.code == 404

    def test_the_cache_is_disabled_so_the_dash_is_not_stale(self, server: ConsoleServer):
        """A cached dash.svg would show a state from whenever the browser last
        asked, which is worse than no display at all."""
        host, port = server.address[0], server.address[1]
        with urllib.request.urlopen(f"http://{host}:{port}/dash.svg", timeout=5) as r:
            assert r.headers.get("Cache-Control") == "no-store"
