# The web display: the dash, the frame log, and the controls.
#
# One process serves everything, and it serves the *generated SVG* rather than
# drawing in the browser. The reason is that there is then exactly one
# implementation of the cluster face - the same `render_svg` that
# `ntorq-console render` writes to a file - so a screenshot taken in the browser
# and an SVG committed to a test cannot disagree about what the cluster looks
# like.
#
# Standard library only. `http.server` is not a production web server and this
# is not a production web server: it binds to loopback by default, it has no
# authentication, and it is a debugging tool for a laptop.

from __future__ import annotations

import json
import threading
import weakref
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import display
from ..cluster import ClusterState, Mode, Trip
from ..sim import SimulatedCluster

STATIC_DIR = Path(__file__).parent / "static"


#: Every behaviour switch, mapped to the object it changes and the attribute it
#: sets on it.
#:
#: Declared as data rather than as a chain of `if` branches so that the page's
#: checkbox list can be checked against it. The mapping is not derived from the
#: attribute names - `challenge` sets `send_challenge`, and `startup-zero` sets
#: an attribute on the cluster rather than on the state - so a name-mangling
#: shortcut would silently set the wrong thing.
SWITCHES: dict[str, tuple[str, str]] = {
    # Frames the real cluster never sends.
    "frame19": ("state", "emit_frame_19"),
    "frame16": ("state", "emit_frame_16"),
    "challenge": ("state", "send_challenge"),
    # The all-zero frames that arrive at connect time.
    "startup_zero": ("cluster", "emit_startup_zero_frames"),
    # How the face is drawn.
    "all_segments": ("render", "all_segments"),
    "debug_overlay": ("render", "show_debug_overlay"),
    "segment_test": ("render", "segment_test"),
}


class ConsoleApp:
    """State shared by every request handler.

    One instance per server. Holds the cluster, the render options, and the
    command table the HTTP layer dispatches into, so adding a control is a
    matter of adding a method rather than editing a request handler.
    """

    def __init__(self, cluster: SimulatedCluster) -> None:
        self.cluster = cluster
        self.render_options = display.RenderOptions()
        self.lock = threading.Lock()
        #: Set by the CLI so the page can show whether BLE is up.
        self.gatt_status = "not started"
        #: Scenarios the page offers, from `scenarios.py`.
        from ..scenarios import SCENARIOS

        self.scenarios = SCENARIOS

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def svg(self) -> str:
        with self.lock:
            return display.render_svg(self.cluster.state, self.render_options)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            data = self.cluster.snapshot()
        data["render"] = {
            "allSegments": self.render_options.all_segments,
            "showDebugOverlay": self.render_options.show_debug_overlay,
            "segmentTest": self.render_options.segment_test,
        }
        data["gatt"] = self.gatt_status
        data["scenarios"] = list(self.scenarios)
        return data

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def command(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Apply one control action.

        Returns a small dict the page shows as a status line, or raises
        ValueError with a message the page displays verbatim. Raising a message
        is deliberate: a control that silently did nothing is worse than one
        that says it could not.

        Both `fuel-bars` and `fuel_bars` work. The page uses hyphens because
        they read better in a URL and a data attribute, and Python names use
        underscores; accepting either means a caller does not have to know which
        convention this module happened to pick.
        """
        key = str(name).replace("-", "_")
        with self.lock:
            handler = getattr(self, f"_cmd_{key}", None)
            if handler is None:
                raise ValueError(
                    f"no control named {name!r}. Available: "
                    + ", ".join(sorted(self.command_names()))
                )
            return handler(params)

    def command_names(self) -> list[str]:
        return sorted(
            attribute[5:]
            for attribute in dir(self)
            if attribute.startswith("_cmd_")
        )

    # --- ride ---------------------------------------------------------

    def _cmd_speed(self, params: dict) -> dict:
        state = self.cluster.state
        state.speed_kmh = float(params.get("value", state.speed_kmh))
        state.throttle_percent = float(
            params.get("throttle", min(100.0, state.speed_kmh * 1.4))
        )
        return {"speed": state.speed_kmh}

    def _cmd_throttle(self, params: dict) -> dict:
        state = self.cluster.state
        state.throttle_percent = float(params.get("value", 0))
        return {"throttle": state.throttle_percent}

    def _cmd_fuel(self, params: dict) -> dict:
        from ..cluster import FUEL_FULL_L

        litres = float(params.get("litres", FUEL_FULL_L))
        state = self.cluster.state
        state.fuel_litres = max(0.0, min(FUEL_FULL_L, litres))
        return {
            "fuelLitres": state.fuel_litres,
            "fuelBars": state.fuel_bars(),
        }

    def _cmd_fuel_bars(self, params: dict) -> dict:
        """Set the transmitted fuel byte directly, bypassing the litre mapping.

        The two are not the same thing, and the difference is one of the open
        questions in the project: the litre thresholds come from the manual,
        while index 6 of `5A 10` is what the cluster actually transmits and what
        the app decodes. Driving the byte directly is how you look at what the
        app renders for a given value.

        The full low nibble, 0 to 15, is accepted rather than just 0 to 6. The
        value that matters most is **7**: it is what the rider's cluster sent in
        every one of 772 captured frames while its own gauge read FUEL EMPTY, and
        it is the value the app is expected to refuse rather than clamp. Refusing
        to let the simulator send it would make the most interesting case the
        one case that cannot be reproduced.

        Values above 6 are reported as out of range in the result, so the page
        can say so, but they are allowed.
        """
        bars = int(params.get("bars", 6))
        if not 0 <= bars <= 15:
            raise ValueError(
                f"index 6 of 5A 10 is one byte and the app reads its low "
                f"nibble, so 0..15, not {bars}"
            )
        self.cluster.state.raw_fuel_override = bars
        return {
            "rawFuelOverride": bars,
            "renderable": bars <= 6,
            **({} if bars <= 6 else {
                "note": (
                    f"{bars} is not a level the six-bar gauge can express; "
                    "the app is expected to drop this rather than clamp it"
                )
            }),
        }

    def _cmd_raw_fuel_clear(self, params: dict) -> dict:
        self.cluster.state.raw_fuel_override = None
        return {"rawFuelOverride": None}

    def _cmd_temp(self, params: dict) -> dict:
        from ..cluster import TEMP_OVER_RANGE_C

        celsius = float(params.get("celsius", 25))
        self.cluster.state.engine_temp_c = max(-20.0, min(TEMP_OVER_RANGE_C + 10, celsius))
        return {"tempC": self.cluster.state.engine_temp_c}

    # --- mode ---------------------------------------------------------

    def _cmd_mode(self, params: dict) -> dict:
        raw = params.get("mode")
        try:
            mode = Mode(raw)
        except ValueError:
            raise ValueError(
                f"unknown mode {raw!r}; one of "
                + ", ".join(m.value for m in Mode)
            ) from None
        self.cluster.state.mode = mode
        return {"mode": mode.value}

    def _cmd_trip(self, params: dict) -> dict:
        raw = params.get("trip")
        try:
            trip = Trip(raw)
        except ValueError:
            raise ValueError(
                f"unknown trip {raw!r}; one of " + ", ".join(t.value for t in Trip)
            ) from None
        self.cluster.state.selected_trip = trip
        return {"trip": trip.value}

    def _cmd_ignition(self, params: dict) -> dict:
        on = bool(params.get("on", True))
        state = self.cluster.state
        state.ignition = on
        state.engine_running = on
        state.ignition_at = state.now
        state.now = 0.0 if on else state.now
        if on:
            state.announce("FUEL EMPTY" if state.fuel_at_reserve() else state.mode.label)
        else:
            state.message = None
            state.segment_icons.clear()
        return {"ignition": state.ignition}

    # --- switches -----------------------------------------------------

    def _cmd_switch(self, params: dict) -> dict:
        """Flip one of the simulator's own behaviour switches.

        These are the switches that make the simulator reproduce something the
        real vehicle demonstrably does *not* do, which is the only way to test
        that the app copes with it.

        [SWITCHES] names every switch and where it lands. `tests/test_page.py`
        checks the page's checkbox list against that table, so a switch added
        here with no control - or a control with no switch - fails rather than
        sitting there looking fine.
        """
        name = str(params.get("name", "")).replace("-", "_")
        value = bool(params.get("value", True))
        target = SWITCHES.get(name)
        if target is None:
            raise ValueError(
                f"unknown switch {params.get('name')!r}; one of "
                + ", ".join(sorted(SWITCHES))
            )
        owner, attribute = target
        setattr(self.cluster if owner == "cluster"
                else self.cluster.state if owner == "state"
                else self.render_options, attribute, value)
        return {"switch": name, "value": value}

    # --- faults -------------------------------------------------------

    def _cmd_fault(self, params: dict) -> dict:
        name = params.get("name")
        state = self.cluster.state
        if name == "clear":
            state.clear_faults()
            return {"fault": "clear"}
        try:
            state.inject(str(name))
        except KeyError:
            raise ValueError(
                f"unknown fault {name!r}; one of "
                + ", ".join(ClusterState.FAULTS)
                + ", clear"
            ) from None
        return {"fault": name}

    # --- phone-facing -------------------------------------------------

    def _cmd_connect(self, params: dict) -> dict:
        self.cluster.connect(params.get("name"))
        return {"connected": True}

    def _cmd_disconnect(self, params: dict) -> dict:
        self.cluster.disconnect()
        return {"connected": False}

    def _cmd_call(self, params: dict) -> dict:
        """Simulate an incoming call, as the phone would report one."""
        from ..cluster import Telltale

        state = self.cluster.state
        state.caller = params.get("name")
        state.telltales.add(Telltale.NOTIFY)
        state.announce(f"CALL FROM {state.caller}", ttl=30.0, kind="call")
        return {"caller": state.caller}

    def _cmd_hangup(self, params: dict) -> dict:
        from ..cluster import Telltale

        state = self.cluster.state
        state.missed_calls += 1 if params.get("missed") else 0
        state.caller = None
        state.telltales.discard(Telltale.NOTIFY)
        return {"missedCalls": state.missed_calls}

    def _cmd_sms(self, params: dict) -> dict:
        from ..cluster import SegIcon, Telltale

        state = self.cluster.state
        state.sms_from = params.get("name")
        state.segment_icons.add(SegIcon.ENVELOPE)
        state.telltales.add(Telltale.NOTIFY)
        state.announce(f"SMS FROM {state.sms_from}", ttl=30.0, kind="sms")
        return {"smsFrom": state.sms_from}

    def _cmd_telltale(self, params: dict) -> dict:
        from ..cluster import Telltale

        name = params.get("name")
        try:
            telltale = Telltale(name)
        except ValueError:
            raise ValueError(
                f"unknown telltale {name!r}; one of "
                + ", ".join(t.value for t in Telltale)
            ) from None
        state = self.cluster.state
        if params.get("on", True):
            state.telltales.add(telltale)
        else:
            state.telltales.discard(telltale)
        return {"telltale": telltale.value}

    # --- scenarios ----------------------------------------------------

    def _cmd_scenario(self, params: dict) -> dict:
        name = params.get("name")
        scenario = self.scenarios.get(name)
        if scenario is None:
            raise ValueError(
                f"unknown scenario {name!r}; one of "
                + ", ".join(sorted(self.scenarios))
            )
        scenario(self.cluster.state)
        return {"scenario": name}

    def _cmd_reset(self, params: dict) -> dict:
        state = self.cluster.state
        fresh = ClusterState(name=state.name, address=state.address)
        # Keep the scenario switches, which are the operator's settings rather
        # than vehicle state.
        fresh.emit_frame_16 = state.emit_frame_16
        fresh.emit_frame_19 = state.emit_frame_19
        fresh.send_challenge = state.send_challenge
        state.__dict__.update(fresh.__dict__)
        self.cluster.connected = False
        return {"reset": True}


class Handler(BaseHTTPRequestHandler):
    """One request. Deliberately small; [ConsoleApp] holds the logic.

    Subclasses built by [ConsoleServer] set `_server_ref`; [app] resolves
    through it on every request so a replaced `ConsoleApp` takes effect.
    """

    _server_ref: weakref.ref[ConsoleServer] | None = None

    @property
    def app(self) -> ConsoleApp:
        if self._server_ref is None:
            raise RuntimeError(
                "Handler was used without a server; build it through "
                "ConsoleServer rather than directly"
            )
        server = self._server_ref()
        if server is None:
            raise RuntimeError("the server was garbage-collected mid-request")
        return server.app

    #: The page polls rather than holding a socket open, so a browser tab can
    #: be closed and reopened without the server noticing.
    server_version = "ntorq-console"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence the default access log; it is one line per poll and drowns
        # the frame log, which is the thing worth reading.
        return

    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path, _, _query = self.path.partition("?")

        if path == "/" or path == "/index.html":
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path == "/dash.svg":
            return self._send(200, self.app.svg().encode("utf-8"), "image/svg+xml")
        if path == "/api/state":
            return self._json(self.app.snapshot())
        if path == "/api/command":
            # A form post or a stray GET lands here. Say what is expected rather
            # than returning 404, because the cause is nearly always a GET from
            # a browser bar or a curl without -X, and "no route" sends people
            # looking in the wrong place.
            return self._json(
                {
                    "error": "/api/command takes a POST with a JSON body "
                    "of {command, params}"
                },
                status=HTTPStatus.METHOD_NOT_ALLOWED,
            )
        if path.startswith("/static/"):
            name = path[len("/static/") :]
            return self._serve_static(name)

        self._json({"error": f"no route for {path}"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            return self._json({"error": f"body is not JSON: {exc}"},
                              status=HTTPStatus.BAD_REQUEST)

        name = body.get("command")
        params = body.get("params") or {}
        try:
            result = self.app.command(str(name), params)
        except ValueError as exc:
            return self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        self._json({"ok": True, "result": result, "state": self.app.snapshot()})

    # ------------------------------------------------------------------

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, name: str, content_type: str = "") -> None:
        # Resolve inside STATIC_DIR and check, so a request for `../../etc/passwd`
        # cannot escape it.
        candidate = (STATIC_DIR / name).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return self._json({"error": "no"}, status=HTTPStatus.NOT_FOUND)
        if not candidate.is_file():
            return self._json({"error": f"no such file: {name}"},
                              status=HTTPStatus.NOT_FOUND)
        guessed = content_type or _guess_type(candidate)
        self._send(HTTPStatus.OK, candidate.read_bytes(), guessed)


def _guess_type(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }.get(path.suffix, "application/octet-stream")


def _parse_query(query: str) -> dict[str, str]:
    from urllib.parse import parse_qs

    return {k: v[0] for k, v in parse_qs(query).items()}


class ConsoleServer:
    """The HTTP server, wrapping [ConsoleApp]."""

    def __init__(
        self,
        cluster: SimulatedCluster,
        host: str = "127.0.0.1",
        port: int = 8765,
        app: ConsoleApp | None = None,
    ) -> None:
        self.app = app if app is not None else ConsoleApp(cluster)
        # The handler resolves `app` through the server rather than capturing
        # it. Binding it as a class attribute at construction means replacing
        # `server.app` afterwards - which a test fixture does, and which a
        # caller wiring up an existing ConsoleApp will do - has no effect at
        # all: the handlers keep talking to the original cluster while the
        # server appears to hold a different one. That failure is invisible
        # until something is set through one path and read through the other.
        handler = type(
            "BoundHandler",
            (Handler,),
            {"_server_ref": weakref.ref(self)},
        )
        self.httpd = ThreadingHTTPServer((host, port), handler)
        #: Bound address, so the CLI can report the port it actually got when
        #: the caller asked for port 0.
        self.address = self.httpd.server_address

    @property
    def url(self) -> str:
        host, port = self.address[0], self.address[1]
        return f"http://{host}:{port}/"

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True, name="ntorq-http"
        )
        thread.start()
        return thread

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
