# ntorq-console

A digital clone of the **TVS Ntorq** instrument cluster.

The rider's scooter is out of service — its battery is flat and it will not turn
over — and that is the situation this exists for. The cluster is not a separate
machine to borrow; it is the thing the app talks to. So this is a clone of the
cluster rather than a mock of the app: it speaks the real Bluetooth LE protocol,
advertises the name the app scans for, and draws the display the manual
documents.

```
Google Maps ──► TVSRideCompanion ──BLE──► ntorq-console ──► SVG dash
   (phone)            (the app)         (this)           (browser)
```

The app is **not modified in any way**. It scans, it connects, it writes to
`00005352-…`, and it reads notifications from `00005354-…`, exactly as it does
against a parked Ntorq. That is the point: the scanner, the service discovery,
the MTU exchange, the write path, the frame stream and every decoder are all
exercised for real.

Two ways to use it, separable on purpose:

| | |
| :--- | :--- |
| **the BLE peripheral** | your laptop is the scooter. Needs `bluetoothd` and an adapter that can advertise. |
| **the web display** | a browser shows the dash and the frame log. No Bluetooth, no hardware, works anywhere. |

---

## Quick start

```bash
git clone https://github.com/athulkrishna2015/ntorq-console.git
cd ntorq-console

# no dependencies, no build step
python3 -m ntorq.cli serve
# → http://127.0.0.1:8765/
```

The display comes up immediately: the dash, a hex frame log in both directions,
sliders for speed, fuel and temperature, fifteen named scenarios, and switches
for the frames the real cluster does *not* send.

To put it on the air as well:

```bash
python3 -m ntorq.cli serve --ble
```

Then in the app, scan and connect. The cluster appears as `TVSBT0000001`.

Run the tests:

```bash
uv venv && uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest
```

---

## What it is for

The hardware pass on 2026-10-13 corrected more assumptions than it confirmed,
and every correction came from reading bytes rather than a screen:

- The cluster advertises **no service UUID**, so a scan filtered on the
  documented one finds nothing and the device must be found by name.
- No pairing, no bonding, and **no `9B F2` challenge** was ever sent.
- The transcribed frame table was **partly wrong**: `0x16` arrives nearly as
  often as the speedometer and is not in it; `0x19` is listed and never arrives.
- Multi-byte fields were read **little-endian**; the cluster sends big-endian.
- Navigation mirroring **had never sent a frame**.

None of those were visible on a dashboard that looked right. This tool is
built so the next one of those is visible immediately: every state is
reachable, and every state that the real vehicle demonstrably does *not* enter
is a switch you can flip.

---

## Findings this produced

### The text area is ten cells wide, not seventeen

Counted off the segment grid in TVS's own display-test illustration, where every
segment is lit: **two rows of ten character cells**. Two independent checks
support it. `CALL FROM 9863571861`, a real message the manual lists, is exactly
twenty characters — it fills the area to the character. And the reference
navigation implementation writes `850m`, `12km`, `45m`, `1h 20m` right-aligned
into eleven-character strings.

TVSRideCompanion's `PacketBuilder` sends **seventeen** characters per row and
treats the row as a single seventeen-wide line. Seventeen does not fit in ten
cells. Whether the cluster wraps it, scrolls it, or truncates it is established
by nothing that has been observed.

The display reports this rather than hiding it. Turn on **debug overlay** and
the dropped-character count appears on the face:

```
text area: 10 cells x 2 rows; 7 character(s) have nowhere to go
```

### `CONNECTION SUCCESSFUL` does not fit either

The manual says the cluster shows `CONNECTION SUCCESSFUL` for three seconds.
That is 21 characters into 20 cells. The cluster has solved this problem for
its own messages somehow — and how it does is what a capture would reveal.
`tests/test_protocol.py` records the mismatch explicitly rather than rounding
it away.

### The outbound byte order is not settled

`PacketBuilder.putUInt16` writes **little-endian**, while every field the
cluster sends is big-endian and is confirmed twice over — by the owner's dash
reading 16593 km against the frame's `02 88 2A`, and by the vendor's own
`DataParser.extractValue`.

A distance of 550 m written little-endian is the bytes `26 02`; read
big-endian the same two bytes are 9730. Nothing observed settles which the
cluster wants. **Every decoded navigation frame reports both readings** and
says the order is unestablished, rather than picking one and being confidently
wrong.

---

## What the display is drawn from

Not traced from anything. Two sources, and every element cites one:

- **TVS's own Ntorq 125 owner manual**, whose display-test illustration shows
  every element with its position, and which enumerates each display element,
  states the bar count for each gauge, names every message, and gives the litre
  thresholds for the fuel gauge. This governs.
- **A photograph** of the owner's own cluster, for the palette and proportions.

The manual gave several facts worth having:

| Manual says | Consequence |
| :--- | :--- |
| "There are six bars" for fuel, and six for temperature | both gauges are six-bar, in every frame this sends |
| 5 L full, 3.2 L half, 1.2 L minimum safe | the bar mapping, and `TRIP F` counting from 0.0 at reserve |
| 12-hour clock with AM/PM | the clock element |
| Mode button steps `TRIP A` → `TRIP B` | the trip selector, with `TRIP F` drawn dimmer because it is not a choice |
| Lap timer starts above 2 km/h | the lap and 0-60 logic |
| Above 2 km/h the non-riding modes hand back to street mode | automatic mode changes |

### Where the drawing is a guess, it says so

The **navigation strip** is a simulator decision. The cluster's navigation zone
has never been observed carrying a live route, and the manual lists the text
area only as "vehicle information and Smart-connect related messages". The
overlay draws the strip dashed and states on the face that its placement is
unverified — where someone debugging the app will see it, rather than in a
comment nobody reads.

---

## Layout

```
ntorq/
  protocol.py      the wire format, both directions. Byte offsets, endianness,
                   checksums. The mirror image of the app's protocol package.
  cluster.py       the cluster's state and its display rules, from the manual.
  sim.py           ticks, frame cadence, the phone link, the log.
  scenarios.py     fifteen named states, one command each.
  display.py       the dash, as SVG.
  pictograms.py    the maneuver table, transcribed from `ManeuverUtils`.
  gatt/
    server.py      a GATT server over BlueZ: service, characteristics, notify.
    advertise.py   the advertising payload. Name only, deliberately.
  web/
    app.py         the HTTP server and the control surface.
    static/        index.html, app.js, console.css. No framework, no build.
tests/             225 tests.
tools/
  preview.py       render a set of states to SVG and PNG.
  dom_check.mjs    run the page's script against the real markup, headless.
```

---

## Design notes

**No runtime dependencies.** The simulator is pure standard library. That is
not asceticism: a debugging aid with a dependency tree is a debugging aid that
will not run when the laptop is misbeinghaving, which is when it is needed.
The GATT server needs `dbus-python` and the Bluetooth stack, and those are
system packages; putting them in the dependency list would install a second
copy of a D-Bus binding that does not share the session bus.

**The SVG is generated server-side.** One implementation of the cluster face —
`render_svg` — used by the browser, by `render` to write a file, and by the
tests. A browser screenshot and a committed SVG cannot disagree about what the
cluster looks like.

**Ticks, not sleeps.** `SimulatedCluster.tick()` advances one 20 ms interval and
returns the frames that are due. No test sleeps, and `iter_frames` is
deterministic: two runs of the same length produce the same stream, which is
what makes a bug reproducible.

**Advertise the name and nothing else.** The real cluster advertised no service
UUID, and that is *why* the app needed a name-based fallback. A simulator that
advertised the service UUID would let a UUID-filtered scan succeed where it
fails against the real vehicle — hiding a bug in the exact code path the
project needs tested. The switch exists to demonstrate the difference, and it
labels itself as wrong when used.

**Fault states go through `inject()`**, which recomputes the derived display
before returning. A fault that only appeared on the next tick would make every
web control and CLI command look like it did nothing.

---

## Tests

225, and the ones that matter are about silence — about the failures that
produce a plausible number rather than an error.

The worst bug in the parent project was an odometer reading ten times low
because `decode` and `Session` each divided by 10. It compiled clean, produced
no warning, passed every on-device smoke test, and was only findable by writing
a test. Everything here is built with that in mind:

- every multi-byte field pinned **big-endian**, and the wrong reading asserted
  too, because the two disagreed by 16.8× on a bike ridden 16593 km
- the captured `5A 10` reproduced byte for byte from a state description
- the fuel byte 7 reproducible, *and* the state model still refusing to render
  it as a level — the value the cluster sent on an empty tank
- `0x19` **off** by default, because this cluster never sent it, and a test
  asserting the app's tiles do not light up from it
- the connect-time all-zero frames reproduced, so a decoder that treats them as
  failures is caught
- a malformed phone write never takes the simulator down
- the page's element ids, command names and switch names checked against the
  server, so a control cannot be dead while looking fine
- `tools/dom_check.mjs` runs the page's actual script against the real markup
  and checks that **every** switch posts **its own** name — a single-switch
  check passes when all seven are bound to one hard-coded name

---

## Licence

Non-commercial, matching TVSRideCompanion. Protocol facts and the maneuver table
come from TVS Connect and from `overclock98/JupiterRideCompanion` (CC BY-NC 4.0);
see TVSRideCompanion/research/SOURCES.md.

This is a clean-room interoperability tool for the user's own vehicle. It does
not redistribute TVS's APK, does not talk to TVS servers, and contains no
routing data, no street names and no device identifiers.