"""The page and the server cannot drift apart silently.

This is the check that catches the class of bug where the web display looks
right, every server-side test passes, and every control is dead because the
JavaScript names a command the server does not have - or spells one of the
server's own names differently. Nothing else in the suite would notice: the
server tests call the handlers directly, and the page has no test runner.

So the page's markup is parsed and every command name in it is resolved against
the real handler table.
"""

from __future__ import annotations

import re

import pytest

from ntorq.cluster import ClusterState
from ntorq.sim import SimulatedCluster
from ntorq.web.app import STATIC_DIR, SWITCHES, ConsoleApp

HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
CSS = (STATIC_DIR / "console.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app() -> ConsoleApp:
    return ConsoleApp(SimulatedCluster(state=ClusterState()))


def _implemented(app: ConsoleApp) -> set[str]:
    return set(app.command_names())


class TestPageMatchesTheServer:
    def test_every_command_the_page_calls_exists(self, app: ConsoleApp):
        """A control that 400s is worse than one that is absent: it looks
        present and does nothing."""
        used = set(re.findall(r'data-cmd="([\w-]+)"', HTML))
        used |= set(re.findall(r'command\("([\w-]+)"', JS))

        implemented = _implemented(app)
        missing = {name for name in used if name.replace("-", "_") not in implemented}
        assert not missing, f"the page calls commands the server lacks: {sorted(missing)}"

def _handled_switches() -> set[str]:
    """The switch names, read from the handler's own table.

    Imported rather than restated: a copy in this file is a second thing to
    keep in step, and a copy that drifts is exactly the bug this module exists
    to catch.

    An earlier version recovered the names by parsing the error message
    `_cmd_switch` raises for an unknown name. That broke the moment the message
    gained a word of prose - the extraction picked up "of", "one" and "switch"
    alongside the real names - and it failed silently in the useful direction,
    because two wrong sets compared equal.
    """
    return set(SWITCHES)


class TestSwitchesMatchTheServer:
    def test_every_switch_the_page_toggles_exists(self, app: ConsoleApp):
        """The switch names are a second namespace with the same failure mode:
        a checkbox that saves to nowhere looks identical to one that works."""
        switches = set(re.findall(r'data-switch="([\w-]+)"', HTML))
        handled = _handled_switches()
        missing = {name for name in switches if name.replace("-", "_") not in handled}
        assert not missing, f"the page toggles unhandled switches: {sorted(missing)}"

    def test_every_handled_switch_reachable_from_the_page_exists(self, app: ConsoleApp):
        """The reverse direction: a switch nobody can reach is dead code."""
        page = {name.replace("-", "_") for name in re.findall(
            r'data-switch="([\w-]+)"', HTML)}
        unreached = _handled_switches() - page
        # `segment-test` has no control of its own: the all-segments checkbox
        # sets it, and a separate one would toggle two flags behind one label.
        assert unreached <= {"segment_test"}, sorted(unreached)

    def test_the_page_reaches_every_implemented_command_somehow(self, app: ConsoleApp):
        """Not a hard requirement - some commands are only for scripts - but a
        command with no route to it from the UI is worth noticing.

        `speed`, `throttle`, `fuel` and `temp` are driven by the sliders, which
        name them in `bindSlider` rather than in a `data-cmd`.
        """
        used = set(re.findall(r'data-cmd="([\w-]+)"', HTML))
        used |= set(re.findall(r'command\("([\w-]+)"', JS))
        for name in ("speed", "throttle", "fuel", "temp"):
            used.add(name)
        implemented = _implemented(app)
        unreached = implemented - {name.replace("-", "_") for name in used}
        # `ignition` and `raw_fuel_clear` are reachable from scenarios and the
        # fuel slider respectively; nothing calls them by name from the page.
        assert unreached <= {"ignition", "raw_fuel_clear"}, sorted(unreached)

    def test_every_scenario_the_server_offers_is_labelled_on_the_page(self, app: ConsoleApp):
        """An unlabelled scenario button reads as `cold-start`, which is fine
        but worse than a label. A server scenario with no button at all is not
        reachable."""
        labels = re.search(r"SCENARIO_LABELS = \{(.*?)\n\};", JS, re.S)
        assert labels, "the page has no scenario label table"
        listed = set(re.findall(r'"([\w-]+)":', labels.group(1)))
        offered = set(app.scenarios)
        assert offered <= listed, f"unlabelled scenarios: {sorted(offered - listed)}"


class TestScenarioNamesAgree:
    def test_the_scenarios_module_and_the_page_agree_on_names(self):
        from ntorq.scenarios import SCENARIOS

        labels = re.search(r"SCENARIO_LABELS = \{(.*?)\n\};", JS, re.S).group(1)
        listed = set(re.findall(r'"([\w-]+)":', labels))
        # The page may know a scenario the server dropped, but must not label a
        # scenario that does not exist: clicking it would 400.
        assert set(SCENARIOS) == listed, {
            "only in the page": sorted(listed - set(SCENARIOS)),
            "only on the server": sorted(set(SCENARIOS) - listed),
        }


class TestPageIntegrity:
    def test_the_page_references_only_files_that_exist(self):
        """A missing stylesheet renders unstyled and looks like a CSS bug."""
        for reference in re.findall(r'(?:href|src)="/static/([^"]+)"', HTML):
            assert (STATIC_DIR / reference).is_file(), reference

    def test_the_page_loads_exactly_the_files_it_needs(self):
        assert "/static/console.css" in HTML
        assert "/static/app.js" in HTML

    def test_the_script_comes_after_the_markup_it_queries(self):
        """A script in the head runs before the body exists, so every
        `getElementById` returns null and nothing binds."""
        assert HTML.index("/static/app.js") > HTML.index("</header>")

    def test_every_element_the_script_queries_exists_in_the_page(self):
        """`getElementById` returning null is the most common way a page like
        this ends up half-dead: no error, no control."""
        queried = set(re.findall(r'el\("([\w-]+)"\)', JS))
        ids = set(re.findall(r'id="([\w-]+)"', HTML))
        missing = {name for name in queried if name not in ids}
        assert not missing, f"the script queries ids that are not in the page: {sorted(missing)}"

    def test_every_dynamic_container_the_script_fills_exists(self):
        for container in ("scenarios", "modes", "trips", "telltales", "faults"):
            assert f'id="{container}"' in HTML, container

    def test_the_frames_and_messages_containers_exist(self):
        for container in ("frames", "messages", "log-summary", "dash"):
            assert f'id="{container}"' in HTML, container

    def test_the_dash_image_points_at_the_served_svg(self):
        assert 'src="/dash.svg"' in HTML

    def test_the_faults_listed_on_the_page_are_real(self, app: ConsoleApp):
        labels = re.search(r"const FAULTS = \[(.*?)\n\];", JS, re.S).group(1)
        listed = set(re.findall(r'\["([\w-]+)"', labels))
        # The page adds `clear`, which the server handles as a fault name.
        offered = set(ClusterState.FAULTS) | {"clear"}
        assert listed <= offered, f"the page offers unknown faults: {sorted(listed - offered)}"

    def test_the_telltales_listed_on_the_page_are_real(self, app: ConsoleApp):
        from ntorq.cluster import Telltale

        labels = re.search(r"const TELLTALES = \[(.*?)\n\];", JS, re.S).group(1)
        listed = set(re.findall(r'\["([\w-]+)"', labels))
        assert listed == {t.value for t in Telltale}, listed.symmetric_difference(
            {t.value for t in Telltale}
        )

    def test_the_modes_listed_on_the_page_are_real(self, app: ConsoleApp):
        from ntorq.cluster import Mode, Trip

        modes = set(re.findall(r'\["([\w-]+)",', re.search(
            r"const MODES = \[(.*?)\n\];", JS, re.S).group(1)))
        assert modes == {m.value for m in Mode}

        trips = set(re.findall(r'\["([\w-]+)",', re.search(
            r"const TRIPS = \[(.*?)\n\];", JS, re.S).group(1)))
        assert trips == {t.value for t in Trip}


class TestNoStaleClaims:
    def test_the_page_does_not_assert_something_the_code_disagrees_with(self):
        """The copy next to the fuel slider states what the app is expected to
        do with a value of 7. If the code ever changes to clamp it, the page
        must change with it - and this is where that is noticed."""
        assert "refuse it" in HTML
        assert "0.07" not in HTML  # no stale numbers pasted into prose

    def test_the_css_has_no_unresolved_variable(self):
        """A `var(--nope)` reference silently falls back to nothing and the
        element loses its colour."""
        defined = set(re.findall(r"(--[\w-]+)\s*:", CSS))
        used = set(re.findall(r"var\((--[\w-]+)", CSS))
        assert used <= defined, f"undefined custom properties: {sorted(used - defined)}"
