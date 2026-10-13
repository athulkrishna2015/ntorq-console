/* The console page.
 *
 * Polls rather than holding a socket open. That is a deliberate trade: a
 * long-lived EventSource means the server has to notice when a tab is closed,
 * and a benchmark tool that logs connection churn while debugging a
 * *Bluetooth* link is adding a second, unrelated thing to debug. A poll every
 * third of a second is invisible and cannot fail that way.
 *
 * The dash is refreshed by swapping the <img> src with a cache-busting query.
 * The SVG is generated server-side; see the comment in index.html for why.
 */

const POLL_MS = 330;

const el = (id) => document.getElementById(id);

const state = {
  snapshot: null,
  /** True while the user is dragging a slider, so polling does not fight them. */
  dragging: false,
};

// ---------------------------------------------------------------------------
// Talking to the server
// ---------------------------------------------------------------------------

async function command(name, params = {}) {
  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: name, params }),
    });
    const payload = await response.json();
    if (payload.error) {
      showError(payload.error);
    }
    if (payload.state) {
      apply(payload.state);
    }
  } catch (error) {
    showError(`${name} failed: ${error}`);
  }
}

async function poll() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    apply(await response.json());
  } catch (error) {
    setBadge("badge-link", `server unreachable: ${error.message}`, "off");
  }
}

let errorTimer = null;

function showError(message) {
  const box = el("messages");
  const line = document.createElement("div");
  line.className = "warn";
  line.textContent = message;
  box.prepend(line);
  while (box.childElementCount > 60) box.lastElementChild.remove();
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => line.remove(), 12000);
}

// ---------------------------------------------------------------------------
// Painting
// ---------------------------------------------------------------------------

function apply(snapshot) {
  state.snapshot = snapshot;
  el("dash").src = `/dash.svg?t=${Date.now()}`;
  paintBadges(snapshot);
  paintFrames(snapshot);
  paintMessages(snapshot);
  paintSwitches(snapshot);
  paintModes(snapshot);
  paintScenarioButtons(snapshot);
}

function setBadge(id, text, cls) {
  const node = el(id);
  node.textContent = text;
  node.className = cls ? `badge ${cls}` : "badge";
}

function paintBadges(snapshot) {
  const gatt = snapshot.gatt || "not started";
  setBadge(
    "badge-gatt",
    `BLE: ${gatt}`,
    /ready|running|advertising/.test(gatt) ? "on" : "off",
  );
  setBadge(
    "badge-link",
    snapshot.connected ? "phone: connected" : "phone: disconnected",
    snapshot.connected ? "on" : "",
  );
  const t = snapshot.clock ?? 0;
  const hhmmss = `${String(Math.floor(t / 3600)).padStart(2, "0")}:${String(
    Math.floor((t % 3600) / 60),
  ).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  setBadge("badge-clock", `cluster t+${hhmmss}`);
}

function paintFrames(snapshot) {
  const frames = snapshot.frames || [];
  const box = el("frames");
  box.textContent = "";
  const summary = el("log-summary");
  if (!frames.length) {
    summary.textContent = "no frames yet";
    return;
  }
  const out = frames.filter((f) => f.dir === "out").length;
  summary.textContent = `${frames.length} shown · ${out} from the cluster`;
  for (const frame of frames.slice(-140)) {
    const line = document.createElement("div");
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = frame.at.toFixed(2).padStart(8);
    const tag = document.createElement("span");
    tag.className = `tag ${frame.dir === "out" ? "out" : "in"}`;
    tag.textContent = frame.dir === "out" ? "cluster" : "phone";
    const hex = document.createElement("span");
    hex.className = "hex";
    hex.textContent = frame.hex;
    const note = document.createElement("span");
    note.className = "note";
    note.textContent = frame.note || "";
    line.append(t, tag, hex, note);
    box.append(line);
  }
  box.scrollTop = box.scrollHeight;
}

function paintMessages(snapshot) {
  // The server keeps its own history; only the ones we have not seen are added,
  // so the box does not rebuild on every poll and steal the scroll position.
  const messages = snapshot.messages || [];
  paintMessages.seen = paintMessages.seen || new Set();
  const box = el("messages");
  for (const message of messages) {
    const key = `${message.at}:${message.text}`;
    if (paintMessages.seen.has(key)) continue;
    paintMessages.seen.add(key);
    const line = document.createElement("div");
    line.textContent = message.text;
    box.prepend(line);
  }
  while (box.childElementCount > 60) box.lastElementChild.remove();
}

function paintSwitches(snapshot) {
  const switches = snapshot.switches || {};
  const render = snapshot.render || {};
  setChecked("sw-frame19", switches.emitFrame19);
  setChecked("sw-frame16", switches.emitFrame16);
  setChecked("sw-challenge", switches.sendChallenge);
  setChecked("sw-startup", switches.startupZeroFrames);
  setChecked("sw-allseg", render.allSegments);
  setChecked("sw-overlay", render.showDebugOverlay);

  const raw = switches.rawFuelOverride;
  const slider = el("rawhigh");
  if (!state.dragging) {
    const value = raw === null ? (snapshot.fuelBars ?? 0) : raw;
    slider.value = String(value);
    el("raw-out").textContent = raw === null ? `${value} (derived)` : String(raw);
  }

  if (!state.dragging) {
    el("speed").value = String(Math.round(snapshot.speedKmh ?? 0));
    el("speed-out").textContent = `${Math.round(snapshot.speedKmh ?? 0)} km/h`;
    el("fuel").value = String(snapshot.fuelLitres ?? 0);
    el("fuel-out").textContent = `${(snapshot.fuelLitres ?? 0).toFixed(1)} L`;
    el("temp").value = String(Math.round(snapshot.tempC ?? 25));
    el("temp-out").textContent = `${Math.round(snapshot.tempC ?? 25)} °C`;
  }
}

function setChecked(id, value) {
  const node = el(id);
  if (node && node.checked !== Boolean(value)) node.checked = Boolean(value);
}

// ---------------------------------------------------------------------------
// Dynamic button groups
// ---------------------------------------------------------------------------

const MODES = [
  ["street", "Street"],
  ["sport", "Sport"],
  ["ride-stats-last", "Ride stats: last lap"],
  ["ride-stats-best", "Ride stats: best lap"],
  ["bt-pair", "BT pair"],
  ["set-clock", "Set clock"],
];

const TRIPS = [
  ["odo", "Odometer"],
  ["a", "TRIP A"],
  ["b", "TRIP B"],
  ["f", "TRIP F"],
];

const TELLTALES = [
  ["eco", "Eco"],
  ["power", "Power"],
  ["high-beam", "High beam"],
  ["turn-left", "Left indicator"],
  ["turn-right", "Right indicator"],
  ["low-fuel", "Low fuel"],
  ["mil", "MIL"],
  ["notify", "Smart-connect LED"],
];

const FAULTS = [
  ["low-fuel", "Low fuel"],
  ["empty-tank", "Empty tank"],
  ["overheating", "Overheating"],
  ["fuel-sensor", "Fuel sensor fault"],
  ["mil", "MIL"],
  ["side-stand", "Side stand down"],
  ["service-due", "Service due"],
  ["battery-low", "Low battery"],
  ["raw-fuel-7", "Fuel byte = 7"],
  ["raw-fuel-2", "Fuel byte = 2"],
  ["zero-odometer", "Zero the odometer"],
];

const SCENARIO_LABELS = {
  "cold-start": "Cold start",
  "running": "Running",
  "city-traffic": "City traffic",
  "reserve-fuel": "Reserve fuel",
  "full-tank": "Full tank",
  "overheating": "Overheating",
  "phone-paired": "Phone paired",
  "incoming-call": "Incoming call",
  "incoming-sms": "Incoming SMS",
  "missed-calls": "Missed calls",
  "bt-pair-mode": "BT pair mode",
  "side-stand-down": "Side stand down",
  "service-due": "Service due",
  "park-and-lock": "Park and lock",
  "navigation-active": "Navigation active",
};

function buildButtons(containerId, entries, handler, pressedFrom) {
  const box = el(containerId);
  if (box.childElementCount) return;
  for (const [value, text] of entries) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.dataset.value = value;
    button.addEventListener("click", () => handler(value, button));
    box.append(button);
  }
}

function paintScenarioButtons(snapshot) {
  buildButtons(
    "scenarios",
    (snapshot.scenarios || []).map((name) => [name, SCENARIO_LABELS[name] || name]),
    (value) => command("scenario", { name: value }),
  );
}

function paintModes(snapshot) {
  buildButtons(
    "modes",
    MODES,
    (value, button) => {
      command("mode", { mode: value });
      markPressed("modes", button);
    },
    true,
  );
  buildButtons("trips", TRIPS, (value, button) => {
    command("trip", { trip: value });
    markPressed("trips", button);
  });
  buildButtons("telltales", TELLTALES, (value, button) => {
    const on = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(on));
    command("telltale", { name: value, on });
  });
  buildButtons("faults", FAULTS, (value) => command("fault", { name: value }));
}

function markPressed(containerId, button) {
  for (const sibling of el(containerId).children) {
    sibling.setAttribute("aria-pressed", String(sibling === button));
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function bindSlider(id, commandName, format, parse) {
  const input = el(id);
  input.addEventListener("pointerdown", () => {
    state.dragging = true;
  });
  const release = () => {
    state.dragging = false;
    command(commandName, { value: parse(input.value) });
  };
  input.addEventListener("change", release);
  // Firefox and Safari fire `input` on every step of a drag; `change` alone
  // leaves the display stuck until the mouse is let go.
  input.addEventListener("input", () => {
    el(`${id}-out`).textContent = format(input.value);
  });
}

function bind() {
  bindSlider(
    "speed",
    "speed",
    (v) => `${v} km/h`,
    (v) => Number(v),
  );
  bindSlider(
    "throttle",
    "throttle",
    (v) => `${v}%`,
    (v) => Number(v),
  );
  bindSlider(
    "fuel",
    "fuel",
    (v) => `${Number(v).toFixed(1)} L`,
    (v) => Number(v),
  );
  bindSlider(
    "temp",
    "temp",
    (v) => `${v} °C`,
    (v) => Number(v),
  );

  const raw = el("rawhigh");
  raw.addEventListener("pointerdown", () => {
    state.dragging = true;
  });
  raw.addEventListener("input", () => {
    el("raw-out").textContent = raw.value;
  });
  raw.addEventListener("change", () => {
    state.dragging = false;
    const value = Number(raw.value);
    if (value > 6) {
      command("fuel-bars", { bars: value });
    } else {
      // 0..6 is a level the gauge can show, so drive the real fuel amount the
      // manual's litre thresholds imply and let the bar follow from it.
      const litres = value === 0 ? 0.1 : (value / 6) * 5.0;
      command("fuel", { litres });
    }
  });

  for (const box of document.querySelectorAll("input[data-switch]")) {
    box.addEventListener("change", () => {
      command("switch", {
        name: box.dataset.switch,
        value: box.checked,
      });
    });
  }

  for (const button of document.querySelectorAll("button[data-cmd]")) {
    button.addEventListener("click", () => {
      const params = {};
      if (button.dataset.name) params.name = button.dataset.name;
      if (button.dataset.missed) params.missed = true;
      command(button.dataset.cmd, params);
    });
  }

  // `key` refreshes immediately, which matters when watching a live frame log
  // and does not otherwise happen for up to a third of a second.
  document.addEventListener("keydown", (event) => {
    if (event.key === "r") poll();
  });
}

bind();
poll();
setInterval(poll, POLL_MS);