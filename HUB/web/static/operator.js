"use strict";
/* Operator touchscreen.
 *
 * The server pushes a full state snapshot over /api/stream (Server-Sent Events)
 * whenever something changes. Views are built only when the mode changes
 * (idle <-> active run), and live updates just refresh numbers, so a form the
 * operator is typing in is never wiped by an update.
 */

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const storage = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

const store = {
  live: null,          // latest snapshot from /api/stream
  offset: 0,           // server clock - browser clock, seconds
  connected: false,
  mode: null,          // "idle" or "active:<run id>"
  shipments: [],
  tab: "production",
  shipmentId: null,
  lineId: null,
  planned: 80,
  seeds: [],           // [{position, expected: [], defect: ""}]
  lastRunId: storage.get("lastRunId", null),
  lastRun: null,
  lastRunFetchedAt: 0,
  showDetails: false,
  stopArmed: false,
  stopTimer: null,
};

const CAMERAS = [
  { key: "c1", name: "Telodyne" },
  { key: "keyence", name: "Keyence" },
  { key: "c2", name: "ELP" },
];
const camName = (key) => (CAMERAS.find((c) => c.key === key) || { name: key }).name;

// --- Helpers ---------------------------------------------------------------------

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg = data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return data;
}

let toastTimer;
function toast(msg, ok = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "show" + (ok ? " ok" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; }, 3500);
}

const pct = (x) => (x === null || x === undefined ? "–" : (x * 100).toFixed(1) + "%");
const fmt = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString());
const serverNow = () => Date.now() / 1000 + store.offset;

function duration(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const hh = Math.floor(seconds / 3600);
  const mm = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return hh ? `${hh}:${mm}:${ss}` : `${mm}:${ss}`;
}

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function runTitle(run) {
  if (run.kind === "validation") return run.sku ? `Validation · ${run.sku}` : "Validation run";
  return run.sku || "Production run";
}

// --- Live connection ---------------------------------------------------------------

function connect() {
  const es = new EventSource("/api/stream");
  es.onmessage = (msg) => {
    store.connected = true;
    store.live = JSON.parse(msg.data);
    store.offset = store.live.now - Date.now() / 1000;
    update();
  };
  es.onerror = () => {  // EventSource reconnects by itself
    store.connected = false;
    renderStatus();
  };
}

function update() {
  const live = store.live;
  const mode = live.active ? `active:${live.active.run.id}` : "idle";
  if (mode !== store.mode) {
    store.mode = mode;
    disarmStop();
    if (live.active) {
      store.lastRunId = live.active.run.id;  // show its summary once it stops
      storage.set("lastRunId", store.lastRunId);
      buildActive();
    } else {
      buildIdle();
    }
  }
  if (live.active) fillActive(live.active);
  else refreshLastRun();
  renderStatus();
  renderBanners();
  renderFinishing();
}

// --- Status bar, banners, finishing strip -------------------------------------------

function renderStatus() {
  const pill = $("#rjpi");
  const text = $("#rjpi-text");
  const rj = store.live && store.live.rjpi;
  if (!store.connected) {
    pill.className = "pill pill-bad"; text.textContent = "HUB connection lost";
  } else if (!rj || !rj.seen) {
    pill.className = "pill pill-wait"; text.textContent = "Waiting for Rj-Pi";
  } else if (rj.online) {
    pill.className = "pill pill-ok"; text.textContent = "Rj-Pi online";
  } else {
    pill.className = "pill pill-bad";
    text.textContent = `Rj-Pi offline (${duration(rj.last_seen_ago)})`;
  }
  const idle = !(store.live && store.live.active);
  $("#belt").textContent = idle && rj && rj.online && rj.cans_on_belt !== null && rj.cans_on_belt !== undefined
    ? `On belt: ${rj.cans_on_belt}` : "";
}

function renderBanners() {
  const live = store.live;
  const banners = [];
  if (live.rjpi.seen && live.rjpi.overflows) {
    banners.push(["bad", `Belt overflow: the Rj-Pi refused ${live.rjpi.overflows} can(s) because 255 were already on the belt. Can numbering may be off.`]);
  }
  if (!live.active && live.unassigned_recent) {
    banners.push(["", `${live.unassigned_recent} can(s) entered in the last 5 minutes with no run active. They aren't counted in any run.`]);
  }
  if (live.rjpi.seen && live.rjpi.missed) {
    banners.push(["", `${live.rjpi.missed} Rj-Pi event(s) were lost on the network since the Rj-Pi started. Counts may be slightly low.`]);
  }
  $("#banners").replaceChildren(...banners.map(([cls, msg]) => h("div", { class: `banner ${cls}` }, msg)));
}

function renderFinishing() {
  $("#finishing").replaceChildren(...store.live.finishing.map((s) =>
    h("span", {}, `Run #${s.run.id} (${runTitle(s.run)}) finishing: `, h("b", {}, s.on_belt),
      " can(s) still on the belt · ", h("b", { class: "ok" }, fmt(s.passed)), " passed · ",
      fmt(s.rejected), " rejected")));
}

// --- Idle view ----------------------------------------------------------------------

function buildIdle() {
  const tabs = h("div", { class: "tabs", role: "tablist" },
    ["production", "validation"].map((tab) => h("button", {
      class: "tab", role: "tab", "aria-selected": String(store.tab === tab),
      onclick: () => { store.tab = tab; buildIdle(); },
    }, tab === "production" ? "Production run" : "Validation run")));
  const body = store.tab === "production" ? buildProduction() : buildValidation();
  $("#view").replaceChildren(h("div", { id: "last-run" }), tabs, ...body);
  if (store.tab === "production") renderShipments(); else renderSeeds();
  store.lastRunFetchedAt = 0;
  refreshLastRun();
}

function operatorInput() {
  return h("input", {
    id: "operator", placeholder: "Operator name", autocomplete: "off",
    value: storage.get("operator", ""),
    oninput: (e) => storage.set("operator", e.target.value),
  });
}

function buildProduction() {
  const picker = h("div", { class: "picker" },
    h("section", { class: "col" },
      h("div", { class: "col-head" }, h("h2", {}, "1 · Shipment"),
        h("button", { class: "btn", onclick: openShipmentDialog }, "+ New shipment")),
      h("div", { class: "list", id: "ship-list" })),
    h("section", { class: "col" },
      h("div", { class: "col-head" }, h("h2", {}, "2 · SKU")),
      h("div", { class: "list", id: "sku-list" })));
  const startbar = h("div", { class: "startbar" }, operatorInput(),
    h("button", { class: "btn btn-start", id: "start-btn", onclick: startProduction }, "START RUN"));
  return [picker, startbar];
}

function renderShipments() {
  const shipList = $("#ship-list");
  if (!shipList) return;
  if (!store.shipments.length) {
    shipList.replaceChildren(h("div", { class: "empty" }, "No shipments yet. Add one with + New shipment."));
  } else {
    shipList.replaceChildren(...store.shipments.map((s) => h("button", {
      class: "card", "aria-pressed": String(s.id === store.shipmentId),
      onclick: () => {
        store.shipmentId = s.id;
        store.lineId = s.lines.length === 1 ? s.lines[0].id : null;
        renderShipments();
      },
    },
      h("div", { class: "title" }, s.supplier),
      h("div", { class: "meta" }, [s.arrival_date && `Arrived ${s.arrival_date}`, s.po_number, s.bill_of_lading]
        .filter(Boolean).join(" · ")),
      h("div", { class: "chips" }, s.lines.map((l) => h("span", { class: "chip" }, l.sku))))));
  }

  const shipment = store.shipments.find((s) => s.id === store.shipmentId);
  const skuList = $("#sku-list");
  if (!shipment) {
    skuList.replaceChildren(h("div", { class: "empty" }, "Pick a shipment first."));
  } else if (!shipment.lines.length) {
    skuList.replaceChildren(h("div", { class: "empty" }, "This shipment has no SKUs."));
  } else {
    skuList.replaceChildren(...shipment.lines.map((l) => h("button", {
      class: "card sku-card", "aria-pressed": String(l.id === store.lineId),
      onclick: () => { store.lineId = l.id; renderShipments(); },
    },
      h("div", { class: "title" }, l.sku),
      h("div", { class: "meta" }, [l.lot_code && `Lot ${l.lot_code}`, l.quantity && `${fmt(l.quantity)} cans`,
        `${l.runs} run${l.runs === 1 ? "" : "s"} so far`].filter(Boolean).join(" · ")))));
  }
  const start = $("#start-btn");
  if (start) start.disabled = !store.lineId;
}

async function loadShipments() {
  try {
    store.shipments = await api("GET", "/api/shipments");
    renderShipments();
  } catch (err) {
    toast(`Couldn't load shipments: ${err.message}`);
  }
}

async function startProduction() {
  try {
    await api("POST", "/api/runs/production", {
      shipment_line_id: store.lineId, operator: $("#operator").value,
    });
    loadShipments();  // refresh "runs so far"
  } catch (err) {
    toast(err.message);
  }
}

// Validation setup

function buildValidation() {
  const plannedInput = h("input", {
    type: "number", min: "1", inputmode: "numeric", value: store.planned, id: "planned",
    oninput: (e) => { store.planned = parseInt(e.target.value, 10) || 0; },
  });
  const step = (delta) => () => {
    store.planned = Math.max(1, (store.planned || 0) + delta);
    plannedInput.value = store.planned;
  };
  const left = h("section", { class: "col" },
    h("div", { class: "col-head" }, h("h2", {}, "Cans in this run")),
    h("div", { class: "stepper" },
      h("button", { class: "btn", onclick: step(-10) }, "−"), plannedInput,
      h("button", { class: "btn", onclick: step(10) }, "+")),
    h("p", { class: "hint" }, "Can #1 is the first can on the belt."),
    h("button", { class: "btn", onclick: randomSeeds }, "Random 5"),
    h("p", { class: "hint" }, "Toggle which camera(s) should catch each defect. All off = any camera counts."));
  const right = h("section", { class: "col" },
    h("div", { class: "col-head" }, h("h2", {}, "Seeded (known-defective) cans"),
      h("button", { class: "btn", onclick: addSeed }, "+ Add")),
    h("div", { class: "list", id: "seed-list" }));
  const startbar = h("div", { class: "startbar" }, operatorInput(),
    h("button", { class: "btn btn-start", onclick: startValidation }, "START VALIDATION"));
  return [h("div", { class: "validate" }, left, right), startbar];
}

function renderSeeds() {
  const list = $("#seed-list");
  if (!list) return;
  if (!store.seeds.length) {
    list.replaceChildren(h("div", { class: "empty" }, "No seeded cans yet. Tap + Add or pick random positions."));
    return;
  }
  list.replaceChildren(...store.seeds.map((seed, i) => h("div", { class: "seed-row" },
    h("input", {
      class: "pos", type: "number", min: "1", inputmode: "numeric", value: seed.position,
      "aria-label": "Position", oninput: (e) => { seed.position = parseInt(e.target.value, 10) || null; },
      onchange: () => { sortSeeds(); renderSeeds(); },
    }),
    h("div", { class: "cams" }, CAMERAS.map((cam) => h("button", {
      class: "cam-toggle", "aria-pressed": String(seed.expected.includes(cam.key)),
      onclick: () => {
        seed.expected = seed.expected.includes(cam.key)
          ? seed.expected.filter((k) => k !== cam.key) : [...seed.expected, cam.key];
        renderSeeds();
      },
    }, cam.name))),
    h("input", {
      placeholder: "Defect (e.g. side dent)", value: seed.defect, autocomplete: "off",
      oninput: (e) => { seed.defect = e.target.value; },
    }),
    h("button", {
      class: "icon-btn", "aria-label": "Remove",
      onclick: () => { store.seeds.splice(i, 1); renderSeeds(); },
    }, "×"))));
}

function sortSeeds() {
  store.seeds.sort((a, b) => (a.position || Infinity) - (b.position || Infinity));
}

function addSeed() {
  const used = new Set(store.seeds.map((s) => s.position));
  let position = 1;
  while (used.has(position)) position++;
  store.seeds.push({ position, expected: [], defect: "" });
  sortSeeds();
  renderSeeds();
}

function randomSeeds() {
  const planned = store.planned || 0;
  const used = new Set(store.seeds.map((s) => s.position));
  const free = [];
  for (let p = 1; p <= planned; p++) if (!used.has(p)) free.push(p);
  for (let i = 0; i < 5 && free.length; i++) {
    const [position] = free.splice(Math.floor(Math.random() * free.length), 1);
    store.seeds.push({ position, expected: [CAMERAS[i % 3].key], defect: "" });
  }
  sortSeeds();
  renderSeeds();
}

async function startValidation() {
  const planned = store.planned;
  if (!store.seeds.length) return toast("Add at least one seeded can");
  const bad = store.seeds.find((s) => !s.position || s.position < 1 || s.position > planned);
  if (bad) return toast(`Every seeded position must be between 1 and ${planned}`);
  try {
    await api("POST", "/api/runs/validation", {
      planned_cans: planned,
      operator: $("#operator").value,
      seeds: store.seeds.map((s) => ({ position: s.position, expected: s.expected, defect: s.defect })),
    });
    store.seeds = [];
  } catch (err) {
    toast(err.message);
  }
}

// Last run summary (idle view)

async function refreshLastRun() {
  const box = $("#last-run");
  if (!box) return;
  if (!store.lastRunId) { box.replaceChildren(); return; }
  const settled = store.lastRun && store.lastRun.run.id === store.lastRunId
    && ["complete", "incomplete"].includes(store.lastRun.run.status);
  const now = Date.now();
  if (!settled && now - store.lastRunFetchedAt > 1000) {
    store.lastRunFetchedAt = now;
    try {
      store.lastRun = await api("GET", `/api/runs/${store.lastRunId}`);
    } catch {
      store.lastRunId = null; storage.set("lastRunId", null);
    }
  }
  renderLastRun();
}

function renderLastRun() {
  const box = $("#last-run");
  const s = store.lastRun;
  if (!box) return;
  if (!s || !store.lastRunId || s.run.id !== store.lastRunId) { box.replaceChildren(); return; }
  const run = s.run;
  const parts = [
    h("div", { class: "summary-head" },
      h("h2", {}, `Last run #${run.id} · ${runTitle(run)}`),
      h("span", { class: `status ${run.status}` }, run.status),
      s.validation ? h("button", { class: "btn btn-ghost", onclick: () => {
        store.showDetails = !store.showDetails; renderLastRun();
      } }, store.showDetails ? "Hide details" : "Details") : null,
      h("button", { class: "btn btn-ghost", onclick: () => {
        store.lastRunId = null; storage.set("lastRunId", null); renderLastRun();
      } }, "Dismiss")),
    h("div", { class: "summary-grid" },
      h("span", {}, "Cans ", h("b", {}, fmt(s.entered))),
      h("span", { class: "passed" }, "Passed inspection ", h("b", {}, fmt(s.passed))),
      h("span", {}, "Rejected ", h("b", {}, fmt(s.rejected))),
      h("span", {}, "Reject rate ", h("b", {}, pct(s.reject_rate))),
      s.on_belt ? h("span", {}, "Still on belt ", h("b", {}, fmt(s.on_belt))) : null,
      h("span", {}, "Flags ", CAMERAS.map((c, i) => [i ? " · " : "", `${c.name} `, h("b", {}, fmt(s.flags[c.key]))]))),
  ];
  const v = s.validation;
  if (v) {
    parts.push(h("div", { class: "summary-grid" },
      h("span", {}, "Seeded cans caught ", h("b", {}, `${v.caught} of ${v.seeds.length}`)),
      h("span", {}, "False rejects ", h("b", {}, v.false_rejects)),
      v.warnings.length ? h("span", { class: "warn-text" }, `⚠ ${v.warnings.length} warning(s)`) : null));
  }
  if (v && store.showDetails) {
    parts.push(h("table", { class: "vtable" },
      h("thead", {}, h("tr", {}, ["Camera", "Expected", "Detected", "Rate", "False flags"].map((t) => h("th", {}, t)))),
      h("tbody", {}, CAMERAS.map((c) => {
        const r = v.cameras[c.key];
        return h("tr", {}, h("td", {}, c.name), h("td", {}, r.expected), h("td", {}, r.detected),
          h("td", {}, pct(r.detection_rate)), h("td", {}, r.false_flags));
      }))));
    for (const w of v.warnings) parts.push(h("p", { class: "warn-text" }, `⚠ ${w}`));
  }
  box.replaceChildren(h("section", { class: "summary" }, parts));
}

// --- Active run view ------------------------------------------------------------------

function buildActive(a = store.live.active, review = false) {
  const run = a.run;
  const tile = (id, label, cls = "") => h("div", { class: `tile ${cls}` },
    h("div", { class: "label" }, label), h("div", { class: "value", id }, "0"));
  const parts = [
    h("div", { class: "run-head" },
      h("span", { class: `kind ${run.kind}` }, run.kind),
      h("span", { class: "what" }, runTitle(run)),
      h("span", { class: "sub" }, [run.supplier, run.arrival_date && `arrived ${run.arrival_date}`,
        `run #${run.id}`, run.operator,
        review && new Date(run.started_at * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }),
      ].filter(Boolean).join(" · ")),
      review ? h("span", { class: `status ${run.status}`, id: "review-status" }, run.status) : null,
      h("span", { class: "elapsed", id: "elapsed", title: review ? "Time from start to stop" : null }, "00:00")),
    h("div", { class: "run-body" },
      h("div", { class: "run-main" },
        // Primary: the three counts the operator watches.
        h("div", { class: "tiles hero" },
          tile("t-entered", "Cans counted"),
          tile("t-passed", "Passed", "good"),
          tile("t-rejected", "Rejected", "bad")),
        // Secondary: per-camera flags.
        h("div", { class: "cam-tiles" }, CAMERAS.map((c) => h("div", { class: "tile cam-tile" },
          h("div", {}, h("div", { class: "label" }, `${c.name} flags`),
            h("div", { class: "value", id: `t-cam-${c.key}` }, "0")),
          h("div", { class: "pct", id: `t-campct-${c.key}` }, ""))))),
      // Tertiary: side rail.
      h("aside", { class: "run-side" },
        tile("t-onbelt", "On belt", "minor"),
        tile("t-rate", "Reject rate", "minor"))),
    run.kind === "validation"
      ? h("section", { class: "seedstrip" },
        h("div", { class: "next-can", id: "next-can" }),
        h("div", { class: "seed-chips", id: "seed-chips" }))
      : null,
    h("section", { class: "rejections" },
      h("div", { class: "rej-head" },
        h("h2", {}, "Rejections"),
        h("span", { class: "rej-note", id: "rej-note" }, "")),
      h("div", { class: "rej-scroll" },
        h("table", { class: "rej-table" },
          h("thead", {}, h("tr", {},
            h("th", {}, "Can #"), h("th", {}, "Time"),
            CAMERAS.map((c) => h("th", { class: "cam-col" }, c.name)),
            h("th", {}, "Flagged by"))),
          h("tbody", { id: "rej-body" })))),
    review ? null : h("button", { class: "btn btn-stop", id: "stop-btn", onclick: onStop }, "STOP RUN"),
  ];
  $("#view").replaceChildren(...parts.filter(Boolean));
}

function fillActive(a) {
  const set = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
  set("t-entered", fmt(a.entered));
  set("t-onbelt", fmt(a.on_belt));
  set("t-passed", fmt(a.passed));
  set("t-rejected", fmt(a.rejected));
  set("t-rate", pct(a.reject_rate));
  for (const c of CAMERAS) {
    set(`t-cam-${c.key}`, fmt(a.flags[c.key]));
    set(`t-campct-${c.key}`, a.exited ? pct(a.flags[c.key] / a.exited) : "");
  }
  tickElapsed();
  fillRejections(a);

  const v = a.validation;
  if (!v) return;
  const next = a.entered + 1;
  const nextSeed = v.seeds.find((s) => s.position === next);
  const nextBox = $("#next-can");
  if (store.review) {
    const count = (r) => v.seeds.filter((s) => s.result === r).length;
    nextBox.className = "next-can";
    nextBox.replaceChildren("Seeded cans: ", h("strong", {}, `${count("caught")} of ${v.seeds.length}`),
      " caught", count("missed") ? ` · ${count("missed")} missed` : "",
      count("lost") ? ` · ${count("lost")} lost` : "", count("not run") ? ` · ${count("not run")} not run` : "");
  } else if (a.entered >= v.planned_cans) {
    nextBox.className = "next-can";
    nextBox.replaceChildren(`All ${v.planned_cans} cans are on the line. Press `, h("strong", {}, "STOP RUN"), ".");
  } else if (nextSeed) {
    const who = nextSeed.expected.length ? nextSeed.expected.map(camName).join(" + ") : "any camera";
    nextBox.className = "next-can seeded";
    nextBox.replaceChildren("Next can ", h("strong", {}, `#${next}`),
      ` is SEEDED: ${nextSeed.defect || "known defect"} (${who})`);
  } else {
    nextBox.className = "next-can";
    nextBox.replaceChildren("Next can ", h("strong", {}, `#${next}`), ` of ${v.planned_cans}`);
  }
  $("#seed-chips").replaceChildren(...v.seeds.map((s) => {
    let cls = "", tail = "";
    if (s.result === "not run") { cls = s.position === next ? "next" : ""; tail = ""; }
    else if (s.result === "pending") { cls = "pending"; tail = " on belt"; }
    else if (s.result === "caught") { cls = "caught"; tail = " ✓"; }
    else if (s.result === "missed") { cls = "missed"; tail = " ✗"; }
    else if (s.result === "lost") { cls = "missed"; tail = " lost"; }
    const who = s.expected.length ? s.expected.map(camName).join("+") : "any";
    return h("span", { class: `seed-chip ${cls}` }, `#${s.position} ${who}${tail}`);
  }));
}

const clockTime = (t) => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–");

function fillRejections(a) {
  const body = $("#rej-body");
  if (!body) return;
  const rows = a.rejections || [];
  const newest = rows.length ? `${rows[0].position}@${rows[0].exited_at}` : null;
  const isNew = store.lastRejection !== undefined && newest !== store.lastRejection;
  store.lastRejection = newest;
  $("#rej-note").textContent = a.rejected > rows.length
    ? `Latest ${rows.length} of ${fmt(a.rejected)}` : "";
  if (!rows.length) {
    body.replaceChildren(h("tr", {}, h("td", { class: "empty", colspan: CAMERAS.length + 3 },
      "No rejections yet.")));
    return;
  }
  body.replaceChildren(...rows.map((r, i) => h("tr", { class: i === 0 && isNew ? "fresh" : "" },
    h("td", { class: "num pos" }, r.position ? `#${r.position}` : "–"),
    h("td", { class: "num" }, clockTime(r.exited_at)),
    CAMERAS.map((c) => h("td", { class: "cam-col" },
      r.flags.includes(c.key) ? h("span", { class: "flag-dot", title: `${c.name} flagged` }, "●") : "")),
    h("td", { class: r.flags.length ? "" : "muted" },
      r.flags.length ? r.flags.map(camName).join(" + ") : "No camera flag"))));
}

function tickElapsed() {
  const a = store.review || (store.live && store.live.active);
  const el = $("#elapsed");
  if (a && el) el.textContent = duration((a.run.stopped_at || serverNow()) - a.run.started_at);
}

// --- Review of a past run (/run/<id>, opened from the dashboard) ---------------------------

const REVIEW_ID = (location.pathname.match(/^\/run\/(\d+)\/?$/) || [])[1];

async function loadReview() {
  let detail;
  try {
    detail = await api("GET", `/api/runs/${REVIEW_ID}`);
  } catch (err) {
    $("#view").replaceChildren(h("div", { class: "empty" }, err.message));
    return;
  }
  store.offset = detail.now - Date.now() / 1000;
  const first = !store.review;
  store.review = detail;
  if (first) buildActive(detail, true);
  const badge = $("#review-status");
  if (badge) { badge.className = `status ${detail.run.status}`; badge.textContent = detail.run.status; }
  fillActive(detail);
  // Still running or finishing: keep it up to date.
  if (detail.run.status === "active" || detail.run.status === "finishing") setTimeout(loadReview, 2000);
}

function startReview() {
  document.body.classList.add("review");
  document.title = `Run #${REVIEW_ID}`;
  $("#rjpi").remove();
  $("#belt").remove();
  const back = $(".toplink");
  back.textContent = "← Dashboard";
  back.addEventListener("click", (ev) => {
    // Came from the dashboard: go back so its filters are kept.
    if (document.referrer && new URL(document.referrer).pathname === "/dashboard") {
      ev.preventDefault();
      history.back();
    }
  });
  $(".brand").after(h("span", { class: "topstat" }, `Run #${REVIEW_ID} · review`));
  loadReview();
}

function onStop() {
  const btn = $("#stop-btn");
  if (!store.stopArmed) {
    store.stopArmed = true;
    btn.classList.add("armed");
    btn.textContent = "TAP AGAIN TO STOP";
    store.stopTimer = setTimeout(disarmStop, 3000);
    return;
  }
  disarmStop();
  api("POST", "/api/runs/stop").catch((err) => toast(err.message));
}

function disarmStop() {
  clearTimeout(store.stopTimer);
  store.stopArmed = false;
  const btn = $("#stop-btn");
  if (btn) { btn.classList.remove("armed"); btn.textContent = "STOP RUN"; }
}

// --- New shipment dialog ----------------------------------------------------------------

function lineRow(line = {}) {
  const row = h("div", { class: "line-row" },
    h("input", { name: "sku", placeholder: "SKU *", value: line.sku || "", autocomplete: "off", "aria-label": "SKU" }),
    h("input", { name: "lot_code", placeholder: "Lot code", autocomplete: "off", "aria-label": "Lot code" }),
    h("input", { name: "quantity", placeholder: "Qty", type: "number", min: "0", inputmode: "numeric", "aria-label": "Quantity" }),
    h("button", { type: "button", class: "icon-btn", "aria-label": "Remove SKU", onclick: () => row.remove() }, "×"));
  return row;
}

function openShipmentDialog() {
  const form = $("#ship-form");
  form.reset();
  form.elements.arrival_date.value = today();
  $("#ship-lines").replaceChildren(lineRow());
  $("#ship-error").textContent = "";
  $("#ship-dialog").showModal();
  form.elements.supplier.focus();
}

async function saveShipment(event) {
  event.preventDefault();
  const form = $("#ship-form");
  const f = form.elements;
  const lines = [...$("#ship-lines").children].map((row) => ({
    sku: row.querySelector('[name="sku"]').value.trim(),
    lot_code: row.querySelector('[name="lot_code"]').value.trim(),
    quantity: row.querySelector('[name="quantity"]').value ? parseInt(row.querySelector('[name="quantity"]').value, 10) : null,
  })).filter((l) => l.sku || l.lot_code || l.quantity);
  if (!f.supplier.value.trim()) { $("#ship-error").textContent = "Supplier is required."; return; }
  if (!lines.length) { $("#ship-error").textContent = "Add at least one SKU."; return; }
  if (lines.some((l) => !l.sku)) { $("#ship-error").textContent = "Every SKU row needs a SKU."; return; }
  const body = { lines };
  for (const name of ["supplier", "arrival_date", "po_number", "bill_of_lading", "carrier", "tracking_number", "notes"]) {
    body[name] = f[name].value.trim();
  }
  try {
    const { id } = await api("POST", "/api/shipments", body);
    $("#ship-dialog").close();
    await loadShipments();
    const added = store.shipments.find((s) => s.id === id);
    store.shipmentId = id;
    store.lineId = added && added.lines.length === 1 ? added.lines[0].id : null;
    renderShipments();
    toast("Shipment saved", true);
  } catch (err) {
    $("#ship-error").textContent = err.message;
  }
}

// --- Start ----------------------------------------------------------------------------------

$("#ship-form").addEventListener("submit", saveShipment);
$("#ship-cancel").addEventListener("click", () => $("#ship-dialog").close());
$("#ship-add-line").addEventListener("click", () => $("#ship-lines").append(lineRow()));

setInterval(() => {
  $("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  tickElapsed();
}, 1000);
$("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

if (REVIEW_ID) {
  startReview();
} else {
  loadShipments();
  connect();
}
