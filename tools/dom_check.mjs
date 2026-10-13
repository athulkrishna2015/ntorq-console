#!/usr/bin/env node
// A headless check of the console page.
//
// The page is plain HTML, CSS and JavaScript with no build step, which is the
// point - but "no build step" also means nothing type-checks it, and a typo in
// an element id or a bad template literal fails at runtime in a browser and
// nowhere else. The Python suite checks the markup against the server; this
// checks that the script actually runs against that markup.
//
// Run it against a live server:
//
//     node tools/dom_check.mjs http://127.0.0.1:8765/
//
// Exits non-zero on the first failure, with the reason.

const base = process.argv[2] || "http://127.0.0.1:8765/";

let failures = 0;

function check(name, condition, detail) {
  if (condition) {
    console.log(`  ok    ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `: ${detail}` : ""}`);
  }
}

// ---------------------------------------------------------------------------
// A DOM small enough to run the page against, and honest about being so.
// ---------------------------------------------------------------------------

class Element {
  constructor(tag, attributes = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = attributes;
    this.children = [];
    this.parent = null;
    this.dataset = {};
    for (const [key, value] of Object.entries(attributes)) {
      if (key.startsWith("data-")) {
        const camel = key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        this.dataset[camel] = value;
      }
    }
    this._listeners = {};
    this.textContent = attributes.text || "";
    this.checked = "checked" in attributes;
    this.value = attributes.value ?? "";
    this.src = attributes.src ?? "";
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      this.dataset[camel] = value;
    }
  }

  getAttribute(name) {
    return this.attributes[name];
  }

  append(...nodes) {
    for (const node of nodes) {
      node.parent = this;
      this.children.push(node);
    }
  }

  prepend(node) {
    node.parent = this;
    this.children.unshift(node);
  }

  remove() {
    if (this.parent) {
      this.parent.children = this.parent.children.filter((c) => c !== this);
      this.parent = null;
    }
  }

  addEventListener(type, handler) {
    (this._listeners[type] ||= []).push(handler);
  }

  dispatch(type, event = {}) {
    for (const handler of this._listeners[type] || []) {
      handler({ ...event, target: this, preventDefault() {} });
    }
  }

  get firstElementChild() {
    return this.children[0] || null;
  }

  get lastElementChild() {
    return this.children[this.children.length - 1] || null;
  }

  get childElementCount() {
    return this.children.length;
  }

  querySelectorAll(selector) {
    const match =
      selector.startsWith("input[") && selector.includes("data-switch")
        ? (el) => el.tagName === "INPUT" && "data-switch" in el.attributes
        : (el) => el.tagName === selector.toUpperCase();
    const out = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (match(child)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

function parseIds(html) {
  const ids = new Map();
  const re = /<(\w+)([^>]*?)\bid="([\w-]+)"([^>]*)>/g;
  let match;
  while ((match = re.exec(html))) {
    const [, tag, before, id, after] = match;
    const attributes = {};
    for (const chunk of `${before} ${after}`.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      attributes[chunk[1]] = chunk[2] ?? "";
    }
    ids.set(id, new Element(tag, attributes));
  }
  return ids;
}

// ---------------------------------------------------------------------------

// An uncaught exception handler that only records is a trap: it suppresses the
// crash, the rest of this module never runs, and the process exits 0 having
// printed three passing checks. Everything below runs inside a try/catch
// instead, and the handler rethrows so a genuine async failure is still loud.
const errors = [];
process.on("uncaughtException", (error) => {
  errors.push(error.message);
  console.log(`  FAIL  uncaught exception: ${error.message}`);
  process.exitCode = 1;
});

const indexResponse = await fetch(new URL("/", base));
const html = await indexResponse.text();
console.log(`fetched ${indexResponse.status} from ${base}`);

const ids = parseIds(html);
console.log(`\nmarkup: ${ids.size} identified elements`);

check(
  "the dash image is present",
  Boolean(html.includes('src="/dash.svg"')),
);

// The state endpoint the page polls.
const stateResponse = await fetch(new URL("/api/state", base));
const state = await stateResponse.json();
check("the state endpoint answers", stateResponse.status === 200);
check("the state carries scenarios", Array.isArray(state.scenarios) && state.scenarios.length > 0);

// Build a document the script can query.
const byId = new Proxy(
  {},
  {
    get(_target, id) {
      if (!ids.has(id)) return null;
      const element = ids.get(id);
      // The two list containers are emptied and filled by the script.
      if (["frames", "messages", "scenarios", "modes", "trips", "telltales", "faults"].includes(id)) {
        element.children = [];
      }
      return element;
    },
  },
);

// Switch checkboxes are found with `document.querySelectorAll`, so the shim has
// to answer that as well as `getElementById`. Leaving it out fails at the first
// `for...of` in `bind()`, which looks like the script being broken rather than
// the harness being incomplete.
const switchBoxes = [...ids.values()].filter(
  (element) => element.tagName === "INPUT" && "data-switch" in element.attributes,
);

const documentListeners = {};

globalThis.document = {
  getElementById: (id) => byId[id] ?? null,
  querySelectorAll: (selector) =>
    selector.includes("data-switch") ? switchBoxes : [],
  addEventListener: (type, handler) => {
    (documentListeners[type] ||= []).push(handler);
  },
  // The script builds every button with `createElement` rather than innerHTML,
  // which is the right choice for a page that has no escaping to get wrong - and
  // it means the shim has to return real nodes with working `dataset`,
  // `append` and `addEventListener` rather than strings.
  createElement: (tag) => new Element(tag),
};
globalThis.window = globalThis;

// fetch, so the script's own polling and command calls work.
const realFetch = globalThis.fetch;
let lastCommand = null;
globalThis.fetch = async (url, options) => {
  if (url === "/api/state") {
    return new Response(JSON.stringify(state), {
      headers: { "Content-Type": "application/json" },
    });
  }
  if (String(url).includes("/api/command")) {
    const body = JSON.parse(options?.body || "{}");
    lastCommand = body;
    const response = await realFetch(url, options);
    return response;
  }
  return realFetch(url, options);
};

globalThis.setInterval = () => 0;

console.log("\nrunning the page script:");
const scriptResponse = await fetch(new URL("/static/app.js", base));
const script = await scriptResponse.text();

// The script's own `poll()` is asynchronous and is invoked at the end of the
// file, so give it a turn to run before anything is asserted.
let ran = true;
let thrown = null;
try {
  // eslint-disable-next-line no-new-func
  new Function(script)();
  await new Promise((resolve) => setTimeout(resolve, 300));
} catch (error) {
  ran = false;
  thrown = error;
}

if (!ran) {
  check("the script ran without throwing", false, thrown.message);
  console.log(
    thrown.stack
      ? thrown.stack.split("\n").slice(0, 6).map((line) => `        ${line}`).join("\n")
      : "",
  );
  console.log(`\n${failures} page check(s) failed`);
  process.exit(1);
}

check("the script ran without throwing", errors.length === 0, errors.join("; "));
check("the frame list was filled", ids.get("frames").childElementCount > 0);

// The scenario and control buttons should now exist.
for (const container of ["scenarios", "modes", "trips", "telltales", "faults"]) {
  check(
    `the ${container} buttons were built`,
    ids.get(container).childElementCount > 0,
    `${ids.get(container).childElementCount} buttons`,
  );
}

check(
  "every scenario got a button",
  ids.get("scenarios").childElementCount === state.scenarios.length,
  `${ids.get("scenarios").childElementCount} buttons for ${state.scenarios.length} scenarios`,
);

// Clicking a scenario button must issue the command the server implements.
const firstScenario = ids.get("scenarios").firstElementChild;
if (firstScenario) {
  firstScenario.dispatch("click");
  await new Promise((resolve) => setTimeout(resolve, 200));
  check(
    "clicking a scenario posts a command",
    lastCommand && lastCommand.command === "scenario",
    JSON.stringify(lastCommand),
  );
  check(
    "the scenario name matches the button",
    lastCommand?.params?.name === firstScenario.dataset.value,
    `${lastCommand?.params?.name} vs ${firstScenario.dataset.value}`,
  );
}

// Sliders must fire their command on change.
const speed = ids.get("speed");
if (speed) {
  speed.value = "77";
  speed.dispatch("change");
  await new Promise((resolve) => setTimeout(resolve, 200));
  check(
    "moving the speed slider posts a command",
    lastCommand && lastCommand.command === "speed" && lastCommand.params.value === 77,
    JSON.stringify(lastCommand),
  );
}

// Every switch checkbox must post its own name.
//
// Checking only one of them is not enough, and the reason is worth recording:
// with a single check, binding every checkbox to one hard-coded switch name
// still passes. The bug is invisible because only the one that was looked at
// looks right, and every other control silently does the same wrong thing.
for (const box of switchBoxes) {
  const expected = box.attributes["data-switch"];
  box.checked = true;
  box.dispatch("change");
  await new Promise((resolve) => setTimeout(resolve, 120));
  check(
    `the ${expected} switch posts itself`,
    lastCommand?.command === "switch" && lastCommand.params.name === expected,
    `posted ${JSON.stringify(lastCommand?.params)}`,
  );
}

console.log(
  failures === 0
    ? "\nall page checks passed"
    : `\n${failures} page check(s) failed`,
);
process.exit(failures === 0 ? 0 : 1);