"use strict";
/* Trends dashboard. Plain SVG charts (no library, works offline).
 *
 * Every chart has a hover/keyboard tooltip and a Table view, so no value is
 * readable only by hovering. Colors come from CSS variables, so light/dark
 * mode switches without re-rendering.
 */

const $ = (sel, root = document) => root.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";

function build(el, attrs, children) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.setAttribute("class", value);
    else if (key === "style") Object.assign(el.style, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
const h = (tag, attrs, ...children) => build(document.createElement(tag), attrs, children);
const s = (tag, attrs, ...children) => build(document.createElementNS(SVGNS, tag), attrs, children);

// --- Formatting --------------------------------------------------------------------------

const pct = (x, digits = 1) => (x === null || x === undefined ? "–" : `${(x * 100).toFixed(digits)}%`);
const pctAxis = (x) => `${Number((x * 100).toFixed(2))}%`;
const int = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString());
function compact(n) {
  if (n >= 1e6) return `${Number((n / 1e6).toFixed(1))}M`;
  if (n >= 1e4) return `${Number((n / 1e3).toFixed(1))}K`;
  return int(n);
}
function parseDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}
const dayShort = (iso) => parseDay(iso).toLocaleDateString([], { month: "short", day: "numeric" });
const dayLong = (iso) => parseDay(iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" });
const when = (t) => (t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "–");

const CAMERA_COLOR = { c1: "var(--cam-c1)", keyence: "var(--cam-keyence)", c2: "var(--cam-c2)" };

function niceTicks(max, target = 4) {
  if (!(max > 0)) max = 0.01;
  const raw = max / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const top = Math.ceil(max / step - 1e-9) * step;
  const ticks = [];
  for (let i = 0; i * step <= top + step / 1e6; i++) ticks.push(i * step);
  return { top, ticks };
}

// --- Tooltip ---------------------------------------------------------------------------------

const tip = $("#tooltip");
function showTip(head, rows, x, y) {
  tip.replaceChildren(
    h("div", { class: "tt-head" }, head),
    ...rows.map((r) => h("div", { class: "tt-row" },
      r.color ? h("span", {
        class: r.key === "rect" ? "key-rect" : "key-line", style: { background: r.color },
      }) : null,
      h("b", {}, r.value), h("span", {}, r.name))));
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  let left = x + 14;
  if (left + box.width > window.innerWidth - 8) left = x - box.width - 14;
  let top = y - box.height - 12;
  if (top < 8) top = y + 16;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${top}px`;
}
function hideTip() { tip.hidden = true; }

// --- Line chart --------------------------------------------------------------------------------

/* x: ["YYYY-MM-DD", ...]; series: [{name, color, values: [number|null]}]. */
function lineChart(el, { x, series, fmt = pct, area = false, height = 230, label }) {
  el.replaceChildren();
  const values = series.flatMap((sr) => sr.values.filter((v) => v !== null && v !== undefined));
  if (!values.length) { el.append(h("div", { class: "empty" }, "No production cans in this period.")); return; }

  const W = Math.max(280, el.clientWidth || 600);
  const directLabels = series.length > 1 && series.length <= 4;
  const m = { l: 46, r: directLabels ? 112 : 16, t: 12, b: 28 };
  const iw = W - m.l - m.r;
  const ih = height - m.t - m.b;
  const { top, ticks } = niceTicks(Math.max(...values));
  const X = (i) => m.l + (x.length === 1 ? iw / 2 : (i * iw) / (x.length - 1));
  const Y = (v) => m.t + ih - (v / top) * ih;

  const svg = s("svg", { viewBox: `0 0 ${W} ${height}`, height, role: "img", tabindex: 0, "aria-label": label });
  el.append(svg);

  const grid = s("g", { class: "grid" });
  const axis = s("g", { class: "axis" });
  for (const t of ticks) {
    if (t > 0) grid.append(s("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }));
    axis.append(s("text", { x: m.l - 8, y: Y(t), dy: "0.32em", "text-anchor": "end" }, pctAxis(t)));
  }
  const nLabels = Math.min(x.length, Math.max(2, Math.floor(iw / 84)));
  const idxs = new Set();
  for (let k = 0; k < nLabels; k++) idxs.add(Math.round((k * (x.length - 1)) / Math.max(1, nLabels - 1)));
  for (const i of idxs) {
    const anchor = x.length === 1 ? "middle" : i === 0 ? "start" : i === x.length - 1 ? "end" : "middle";
    axis.append(s("text", { x: X(i), y: height - 8, "text-anchor": anchor }, dayShort(x[i])));
  }
  svg.append(grid, s("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0) }), axis);

  // Lines (broken at days with no data), area wash for a single series.
  const ends = [];
  for (const sr of series) {
    const segments = [];
    let cur = [];
    sr.values.forEach((v, i) => {
      if (v === null || v === undefined) { if (cur.length) segments.push(cur); cur = []; }
      else cur.push([X(i), Y(v)]);
    });
    if (cur.length) segments.push(cur);
    for (const seg of segments) {
      if (area && series.length === 1 && seg.length > 1) {
        const d = `M${seg[0][0]},${Y(0)} ` + seg.map(([px, py]) => `L${px},${py}`).join(" ") + ` L${seg[seg.length - 1][0]},${Y(0)} Z`;
        svg.append(s("path", { d, style: { fill: sr.color, opacity: 0.1 } }));
      }
      if (seg.length === 1) {
        svg.append(s("circle", { cx: seg[0][0], cy: seg[0][1], r: 3, style: { fill: sr.color } }));
      } else {
        svg.append(s("path", {
          d: "M" + seg.map(([px, py]) => `${px},${py}`).join(" L"),
          style: { fill: "none", stroke: sr.color, strokeWidth: 2, strokeLinejoin: "round", strokeLinecap: "round" },
        }));
      }
    }
    let last = sr.values.length - 1;
    while (last >= 0 && (sr.values[last] === null || sr.values[last] === undefined)) last--;
    if (last >= 0) {
      svg.append(s("circle", { cx: X(last), cy: Y(sr.values[last]), r: 4, style: { fill: sr.color, stroke: "var(--surface)", strokeWidth: 2 } }));
      ends.push({ sr, i: last, y: Y(sr.values[last]) });
    }
  }

  // Direct end labels, only when they don't collide (the legend covers the rest).
  if (directLabels && ends.length) {
    const sorted = [...ends].sort((a, b) => a.y - b.y);
    const collide = sorted.some((e, k) => k && e.y - sorted[k - 1].y < 15);
    if (!collide) {
      for (const e of ends) {
        svg.append(s("text", { x: X(e.i) + 10, y: e.y, dy: "0.32em" },
          s("tspan", { class: "lbl-strong" }, fmt(e.sr.values[e.i])),
          s("tspan", { class: "lbl", dx: 5 }, e.sr.name)));
      }
    }
  }

  // Crosshair + tooltip (pointer and keyboard).
  const cross = s("line", { class: "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" });
  const dots = s("g", { visibility: "hidden" });
  svg.append(cross, dots);
  let current = null;
  function show(i, clientX, clientY) {
    current = i;
    cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i));
    cross.setAttribute("visibility", "visible");
    dots.replaceChildren(...series.filter((sr) => sr.values[i] !== null && sr.values[i] !== undefined)
      .map((sr) => s("circle", { cx: X(i), cy: Y(sr.values[i]), r: 4, style: { fill: sr.color, stroke: "var(--surface)", strokeWidth: 2 } })));
    dots.setAttribute("visibility", "visible");
    const rows = series.map((sr) => ({ color: sr.color, value: fmt(sr.values[i]), name: sr.name, raw: sr.values[i] ?? -1 }))
      .sort((a, b) => b.raw - a.raw);
    if (series.length === 1 && series[0].extra) rows.push(...series[0].extra(i));
    if (clientX === undefined) {
      const r = svg.getBoundingClientRect();
      clientX = r.left + (X(i) / W) * r.width;
      clientY = r.top + m.t + 20;
    }
    showTip(dayLong(x[i]), rows, clientX, clientY);
  }
  function hide() {
    current = null;
    cross.setAttribute("visibility", "hidden");
    dots.setAttribute("visibility", "hidden");
    hideTip();
  }
  const overlay = s("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
  overlay.addEventListener("pointermove", (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(x.length - 1, Math.round(((px - m.l) / iw) * (x.length - 1))));
    show(i, ev.clientX, ev.clientY);
  });
  overlay.addEventListener("pointerleave", hide);
  svg.append(overlay);
  svg.addEventListener("focus", () => show(x.length - 1));
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (ev) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[ev.key];
    if (step) { ev.preventDefault(); show(Math.max(0, Math.min(x.length - 1, (current ?? x.length - 1) + step))); }
    if (ev.key === "Home") show(0);
    if (ev.key === "End") show(x.length - 1);
    if (ev.key === "Escape") hide();
  });
}

// --- Horizontal bars -----------------------------------------------------------------------------

function barPath(x, y, w, t, r = 4) {
  // Square at the baseline (left), 4px rounded data end (right).
  if (w <= 0) return "";
  r = Math.min(r, w, t / 2);
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + t - r} Q${x + w},${y + t} ${x + w - r},${y + t} H${x} Z`;
}

function fitText(textEl, maxWidth) {
  let str = textEl.textContent;
  if (textEl.getComputedTextLength() <= maxWidth) return;
  while (str.length > 1 && textEl.getComputedTextLength() > maxWidth) {
    str = str.slice(0, -1);
    textEl.textContent = `${str.trimEnd()}…`;
  }
}

/* rows: [{label, sub, value, color, flag, tip: [{name, value}]}] */
function barChart(el, rows, { fmt = pct, ref = null, label }) {
  el.replaceChildren();
  if (!rows.length) { el.append(h("div", { class: "empty" }, "No production cans in this period.")); return; }
  const W = Math.max(300, el.clientWidth || 600);
  const rowH = rows.some((r) => r.sub) ? 42 : 32;
  const labelW = Math.min(250, W * 0.36);
  const narrow = W < 520;
  const valueW = rows.some((r) => r.flag) ? (narrow ? 96 : 132) : 60;
  const x0 = labelW + 10;
  const x1 = W - valueW;
  const topPad = ref ? 22 : 4;
  const axisH = 22;
  const height = topPad + rows.length * rowH + axisH;
  const max = Math.max(...rows.map((r) => r.value || 0), ref ? ref.value : 0);
  const { top, ticks } = niceTicks(max, Math.max(1, Math.min(4, Math.floor((x1 - x0) / 56))));
  const X = (v) => x0 + (v / top) * (x1 - x0);
  const svg = s("svg", { viewBox: `0 0 ${W} ${height}`, height, role: "img", "aria-label": label });
  el.append(svg);

  const grid = s("g", { class: "grid" });
  const axis = s("g", { class: "axis" });
  for (const t of ticks) {
    if (t > 0) grid.append(s("line", { x1: X(t), x2: X(t), y1: topPad, y2: height - axisH }));
    axis.append(s("text", { x: X(t), y: height - 6, "text-anchor": "middle" }, pctAxis(t)));
  }
  svg.append(grid, axis, s("line", { class: "baseline", x1: x0, x2: x0, y1: topPad, y2: height - axisH }));

  const thick = 16;
  rows.forEach((r, k) => {
    const y = topPad + k * rowH;
    const g = s("g", { class: "bar-hit", tabindex: 0, role: "img", "aria-label": `${r.label}${r.sub ? `, ${r.sub}` : ""}: ${fmt(r.value)}` });
    g.append(s("rect", { class: "hit", x: 0, y: y + 1, width: W, height: rowH - 2, rx: 4, fill: "transparent" }));
    const name = s("text", { x: 0, y: r.sub ? y + rowH / 2 - 4 : y + rowH / 2, dy: "0.32em", style: { fill: "var(--text)", fontSize: "13px" } }, r.label);
    g.append(name);
    let subEl = null;
    if (r.sub) {
      subEl = s("text", { class: "lbl-muted", x: 0, y: y + rowH / 2 + 11, dy: "0.32em" }, r.sub);
      g.append(subEl);
    }
    const by = y + (rowH - thick) / 2;
    const w = Math.max(0, X(r.value || 0) - x0);
    g.append(s("path", { class: "bar", d: barPath(x0, by, w, thick), style: { fill: r.color } }));
    const vt = s("text", { x: x0 + w + 6, y: y + rowH / 2, dy: "0.32em" },
      s("tspan", { class: "lbl-strong" }, fmt(r.value)));
    if (r.flag) {
      vt.append(s("tspan", { class: "flag-icon", dx: 8 }, "▲"),
        s("tspan", { class: "lbl", dx: 3 }, narrow ? r.flag.replace(" average", "") : r.flag));
    }
    g.append(vt);
    const showRow = (cx, cy) => showTip(r.sub ? `${r.label} · ${r.sub}` : r.label,
      [{ color: r.color, key: "rect", value: fmt(r.value), name: "reject rate" }, ...(r.tip || [])], cx, cy);
    g.addEventListener("pointermove", (ev) => showRow(ev.clientX, ev.clientY));
    g.addEventListener("pointerleave", hideTip);
    g.addEventListener("focus", () => {
      const b = svg.getBoundingClientRect();
      showRow(b.left + (X(r.value || 0) / W) * b.width, b.top + ((y + rowH / 2) / height) * b.height);
    });
    g.addEventListener("blur", hideTip);
    svg.append(g);
    fitText(name, labelW);
    if (subEl) fitText(subEl, labelW);
  });

  if (ref) {
    svg.append(
      s("line", { x1: X(ref.value), x2: X(ref.value), y1: topPad - 4, y2: height - axisH, style: { stroke: "var(--text-2)", strokeWidth: 1 } }),
      s("text", { class: "lbl", x: X(ref.value), y: 10, "text-anchor": "middle" }, `${ref.label} ${fmt(ref.value)}`));
  }
}

/* Two-part 100% bars (emphasis + de-emphasis), one per row with a color key. */
function splitBars(el, rows, { label }) {
  el.replaceChildren();
  if (!rows.some((r) => r.total)) { el.append(h("div", { class: "empty" }, "No flagged cans in this period.")); return; }
  const W = Math.max(300, el.clientWidth || 500);
  const rowH = 40;
  const labelW = 96;
  const valueW = 92;
  const x0 = labelW;
  const x1 = W - valueW;
  const height = rows.length * rowH;
  const svg = s("svg", { viewBox: `0 0 ${W} ${height}`, height, role: "img", "aria-label": label });
  el.append(svg);
  const thick = 18;
  rows.forEach((r, k) => {
    const y = k * rowH;
    const g = s("g", { class: "bar-hit", tabindex: 0, role: "img", "aria-label": `${r.label}: ${pct(r.share, 0)} ${r.partA}` });
    g.append(s("rect", { class: "hit", x: 0, y: y + 1, width: W, height: rowH - 2, rx: 4, fill: "transparent" }));
    g.append(s("rect", { x: 0, y: y + rowH / 2 - 5, width: 10, height: 10, rx: 2, style: { fill: r.color } }));
    g.append(s("text", { x: 16, y: y + rowH / 2, dy: "0.32em", style: { fill: "var(--text)", fontSize: "13px" } }, r.label));
    const by = y + (rowH - thick) / 2;
    const span = x1 - x0;
    const wa = r.total ? (r.a / r.total) * span : 0;
    const gap = r.a && r.b ? 2 : 0;
    // Part A: square left end (baseline), rounded only if it fills the bar.
    if (wa > 0) g.append(s("path", { class: "bar", d: r.b ? `M${x0},${by} h${wa - gap} v${thick} h${-(wa - gap)} Z` : barPath(x0, by, wa, thick), style: { fill: "var(--emph)" } }));
    if (r.b) g.append(s("path", { class: "bar", d: barPath(x0 + wa, by, span - wa, thick), style: { fill: "var(--deemph)" } }));
    g.append(s("text", { x: x1 + 8, y: y + rowH / 2, dy: "0.32em" },
      s("tspan", { class: "lbl-strong" }, pct(r.share, 0)), s("tspan", { class: "lbl", dx: 4 }, "alone")));
    const showRow = (cx, cy) => showTip(r.label, [
      { value: int(r.total), name: "cans flagged" },
      { color: "var(--emph)", key: "rect", value: int(r.a), name: r.partA },
      { color: "var(--deemph)", key: "rect", value: int(r.b), name: r.partB },
    ], cx, cy);
    g.addEventListener("pointermove", (ev) => showRow(ev.clientX, ev.clientY));
    g.addEventListener("pointerleave", hideTip);
    g.addEventListener("focus", () => {
      const b = svg.getBoundingClientRect();
      showRow(b.left + (x1 / W) * b.width, b.top + ((y + rowH / 2) / height) * b.height);
    });
    g.addEventListener("blur", hideTip);
    svg.append(g);
  });
}

// --- Tables -------------------------------------------------------------------------------------

/* columns: [{label, n: bool, color}]; rows: arrays of cells (text or Node).
   links: optional array of URLs, one per row, that make the whole row clickable. */
function table(columns, rows, empty = "Nothing in this period.", links = null) {
  if (!rows.length) return h("div", { class: "empty" }, empty);
  const rowAttrs = (i) => {
    const href = links && links[i];
    if (!href) return {};
    const open = (ev) => {
      if (ev.target.closest("a")) return;  // the cell's own link handles it
      if (ev.metaKey || ev.ctrlKey) window.open(href, "_blank");
      else location.href = href;
    };
    return { class: "link-row", title: "Open this run", onclick: open,
      onkeydown: (ev) => { if (ev.key === "Enter") open(ev); }, tabindex: "0" };
  };
  return h("table", {},
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.n ? "n" : null, scope: "col" },
      c.color ? h("span", { class: "swatch", style: { background: c.color } }) : null, c.label)))),
    h("tbody", {}, rows.map((row, r) => h("tr", rowAttrs(r),
      row.map((cell, i) => h("td", { class: columns[i].n ? "n" : null }, cell))))));
}

const runLink = (id) => h("a", { class: "run-link", href: `/run/${id}` }, `#${id}`);

function statusCell(status) {
  const icon = { complete: "✓", incomplete: "⚠", finishing: "…", active: "●" }[status] || "";
  return h("span", { class: `status ${status}` }, h("span", { class: "status-icon", "aria-hidden": "true" }, icon), status);
}

function warnCell(n) {
  return n ? h("span", { class: "warn-cell" }, h("span", { class: "status-icon", "aria-hidden": "true" }, "⚠"), int(n)) : h("span", { class: "muted" }, "0");
}

// --- State & data -------------------------------------------------------------------------------

const params = new URLSearchParams(location.search);
const state = {
  days: parseInt(params.get("days"), 10) || 30,
  supplier: params.get("supplier") || "",
  sku: params.get("sku") || "",
  data: null,
  tables: {},
  allShipments: false,
  allRuns: false,
  allHealth: false,
  loading: false,
};

function query() {
  const q = new URLSearchParams({ days: state.days });
  if (state.supplier) q.set("supplier", state.supplier);
  if (state.sku) q.set("sku", state.sku);
  return q.toString();
}

async function load() {
  if (state.loading) return;
  state.loading = true;
  $("#main").classList.add("loading");
  history.replaceState(null, "", `?${query()}`);
  $("#export").href = `/api/export/runs.csv?${query()}`;
  try {
    const res = await fetch(`/api/dashboard?${query()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
    render();
    $("#updated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  } catch (err) {
    $("#updated").textContent = `Couldn't refresh (${err.message})`;
  } finally {
    state.loading = false;
    $("#main").classList.remove("loading");
  }
}

function render() {
  renderFilters();
  renderKpis();
  renderCharts();
  renderValidation();
  renderRuns();
  renderHealth();
}

function renderFilters() {
  for (const btn of $("#range").children) btn.setAttribute("aria-checked", String(Number(btn.dataset.days) === state.days));
  const fill = (sel, values, current, allLabel) => {
    const el = $(sel);
    el.replaceChildren(h("option", { value: "" }, allLabel), ...values.map((v) => h("option", { value: v }, v)));
    el.value = values.includes(current) ? current : "";
  };
  fill("#f-supplier", state.data.options.suppliers, state.supplier, "All suppliers");
  fill("#f-sku", state.data.options.skus, state.sku, "All SKUs");
}

function renderKpis() {
  const d = state.data;
  const t = d.totals;
  const p = d.previous;
  const span = `previous ${d.period.days} days`;
  let delta;
  if (t.reject_rate === null || p.reject_rate === null) {
    delta = h("div", { class: "delta" }, p.cans ? "" : `No data for the ${span}`);
  } else {
    const diff = (t.reject_rate - p.reject_rate) * 100;
    const up = diff > 0.05;
    const down = diff < -0.05;
    delta = h("div", { class: `delta ${up ? "bad" : down ? "good" : ""}` },
      h("span", { class: "icon", "aria-hidden": "true" }, up ? "▲" : down ? "▼" : "■"),
      h("b", {}, `${up ? "+" : ""}${diff.toFixed(1)} pts`), `vs ${span} (${pct(p.reject_rate)})`);
  }
  const v = d.validation;
  const seeded = v.reduce((a, r) => a + r.seeded, 0);
  const caught = v.reduce((a, r) => a + r.caught, 0);
  let weakest = null;
  for (const cam of d.cameras) {
    const exp = v.reduce((a, r) => a + r.cameras[cam.key].expected, 0);
    const det = v.reduce((a, r) => a + r.cameras[cam.key].detected, 0);
    if (exp && (!weakest || det / exp < weakest.rate)) weakest = { name: cam.name, rate: det / exp };
  }
  const kpi = (label, value, extra, hero = false) => h("div", { class: `kpi${hero ? " hero" : ""}` },
    h("div", { class: "label" }, label), h("div", { class: "value" }, value), extra || null);
  $("#kpis").replaceChildren(
    kpi("Reject rate", pct(t.reject_rate), delta, true),
    kpi("Cans inspected", compact(t.cans), h("div", { class: "delta" }, `${compact(p.cans)} in the ${span}`)),
    kpi("Cans rejected", compact(t.rejected)),
    kpi("Production runs", int(t.runs)),
    kpi("Validation: seeded cans caught", seeded ? `${caught} of ${seeded}` : "–",
      h("div", { class: "delta" }, seeded
        ? (weakest ? `Lowest: ${weakest.name} at ${pct(weakest.rate, 0)}` : "")
        : "No validation runs in this period")),
  );
}

function cardMode(id) {
  const card = document.getElementById(id);
  const btn = card.querySelector("[data-table]");
  const tableMode = !!state.tables[id];
  if (btn) btn.setAttribute("aria-pressed", String(tableMode));
  return { card, chart: card.querySelector(".chart"), tableMode };
}

function renderCharts() {
  const d = state.data;
  const days = d.daily.map((r) => r.day);

  // Reject rate per day
  {
    const { chart, tableMode } = cardMode("c-reject");
    if (tableMode) {
      chart.replaceChildren(table(
        [{ label: "Day" }, { label: "Cans", n: true }, { label: "Rejected", n: true }, { label: "Reject rate", n: true }],
        [...d.daily].reverse().filter((r) => r.cans).map((r) => [dayLong(r.day), int(r.cans), int(r.rejected), pct(r.reject_rate)])));
    } else {
      lineChart(chart, {
        x: days, area: true, label: "Reject rate per day",
        series: [{
          name: "reject rate", color: "var(--accent)", values: d.daily.map((r) => r.reject_rate),
          extra: (i) => [{ value: int(d.daily[i].cans), name: "cans" }, { value: int(d.daily[i].rejected), name: "rejected" }],
        }],
      });
    }
  }

  // Flag rate by camera
  {
    const { card, chart, tableMode } = cardMode("c-cameras");
    card.querySelector(".legend").replaceChildren(...d.cameras.map((c) =>
      h("span", {}, h("span", { class: "key-line", style: { background: CAMERA_COLOR[c.key] } }), c.name)));
    if (tableMode) {
      chart.replaceChildren(table(
        [{ label: "Day" }, { label: "Cans", n: true },
          ...d.cameras.map((c) => ({ label: c.name, n: true, color: CAMERA_COLOR[c.key] }))],
        [...d.daily].reverse().filter((r) => r.cans).map((r) => [dayLong(r.day), int(r.cans),
          ...d.cameras.map((c) => pct(r.flag_rates[c.key]))])));
    } else {
      lineChart(chart, {
        x: days, label: "Flag rate by camera per day",
        series: d.cameras.map((c) => ({ name: c.name, color: CAMERA_COLOR[c.key], values: d.daily.map((r) => r.flag_rates[c.key]) })),
      });
    }
  }

  // Shipments
  {
    const { card, chart, tableMode } = cardMode("c-shipments");
    const more = card.querySelector(".more");
    const avg = d.totals.reject_rate;
    if (tableMode) {
      more.hidden = true;
      chart.replaceChildren(table(
        [{ label: "Supplier" }, { label: "Arrived" }, { label: "SKU" }, { label: "PO" }, { label: "Lot" },
          { label: "Runs", n: true }, { label: "Cans", n: true }, { label: "Rejected", n: true }, { label: "Reject rate", n: true },
          { label: "vs average", n: true }, ...d.cameras.map((c) => ({ label: `${c.name} flags`, n: true, color: CAMERA_COLOR[c.key] }))],
        d.shipments.map((r) => [r.supplier, r.arrival_date || "–", r.sku, r.po_number || "–", r.lot_code || "–",
          int(r.runs), int(r.cans), int(r.rejected), pct(r.reject_rate),
          r.outlier ? h("span", { class: "warn-cell" }, h("span", { class: "status-icon" }, "▲"), `${r.vs_overall.toFixed(1)}×`)
            : (r.vs_overall ? `${r.vs_overall.toFixed(1)}×` : "–"),
          ...d.cameras.map((c) => int(r[c.key]))])));
    } else {
      const limit = 12;
      const list = state.allShipments ? d.shipments : d.shipments.slice(0, limit);
      more.hidden = d.shipments.length <= limit;
      more.textContent = state.allShipments ? "Show top 12" : `Show all ${d.shipments.length}`;
      barChart(chart, list.map((r) => ({
        label: r.supplier,
        sub: [r.arrival_date && `arrived ${dayShort(r.arrival_date)}`, r.sku, r.po_number].filter(Boolean).join(" · "),
        value: r.reject_rate,
        color: r.outlier ? "var(--critical)" : "var(--accent)",
        flag: r.outlier ? `${r.vs_overall.toFixed(1)}× average` : null,
        tip: [{ value: int(r.cans), name: "cans" }, { value: int(r.rejected), name: "rejected" },
          ...(r.lot_code ? [{ value: r.lot_code, name: "lot" }] : [])],
      })), { ref: avg === null ? null : { value: avg, label: "Average" }, label: "Reject rate by shipment" });
    }
  }

  // Suppliers
  {
    const { chart, tableMode } = cardMode("c-suppliers");
    if (tableMode) {
      chart.replaceChildren(table(
        [{ label: "Supplier" }, { label: "Cans", n: true }, { label: "Rejected", n: true }, { label: "Reject rate", n: true }],
        d.suppliers.map((r) => [r.supplier, int(r.cans), int(r.rejected), pct(r.reject_rate)])));
    } else {
      barChart(chart, d.suppliers.map((r) => ({
        label: r.supplier, value: r.reject_rate, color: "var(--accent)",
        tip: [{ value: int(r.cans), name: "cans" }, { value: int(r.rejected), name: "rejected" }],
      })), { ref: d.totals.reject_rate === null ? null : { value: d.totals.reject_rate, label: "Average" }, label: "Reject rate by supplier" });
    }
  }

  // Camera agreement
  {
    const { card, chart, tableMode } = cardMode("c-agreement");
    card.querySelector(".legend").replaceChildren(
      h("span", {}, h("span", { class: "key-rect", style: { background: "var(--emph)" } }), "Only this camera"),
      h("span", {}, h("span", { class: "key-rect", style: { background: "var(--deemph)" } }), "Another camera agreed"));
    const name = (key) => d.cameras.find((c) => c.key === key).name;
    if (tableMode) {
      chart.replaceChildren(table(
        [{ label: "Camera" }, { label: "Cans flagged", n: true }, { label: "Only this camera", n: true },
          { label: "Another agreed", n: true }, { label: "Alone", n: true }],
        d.agreement.map((r) => [h("span", {}, h("span", { class: "swatch", style: { background: CAMERA_COLOR[r.camera] } }), name(r.camera)),
          int(r.flags), int(r.alone), int(r.shared), pct(r.alone_share, 0)])));
    } else {
      splitBars(chart, d.agreement.map((r) => ({
        label: name(r.camera), color: CAMERA_COLOR[r.camera], total: r.flags, a: r.alone, b: r.shared,
        share: r.alone_share, partA: "only this camera", partB: "another camera agreed",
      })), { label: "Camera agreement" });
    }
  }
}

function detectionCell(c) {
  if (!c.expected) return h("span", { class: "muted" }, "–");
  const rate = c.detected / c.expected;
  return h("span", {}, rate < 1 ? h("span", { class: "warn-cell" }, h("span", { class: "status-icon", "aria-hidden": "true" }, "⚠")) : null,
    `${c.detected}/${c.expected} `, h("span", { class: "muted" }, `(${pct(rate, 0)})`));
}

function renderValidation() {
  const d = state.data;
  $("#c-validation .table-wrap").replaceChildren(table(
    [{ label: "Started" }, { label: "Run", n: true }, { label: "Operator" }, { label: "Cans", n: true },
      { label: "Seeded caught", n: true },
      ...d.cameras.map((c) => ({ label: c.name, n: true, color: CAMERA_COLOR[c.key] })),
      { label: "False rejects", n: true }, { label: "Notes" }],
    d.validation.map((r) => [when(r.started_at), runLink(r.run_id), r.operator || "–",
      r.entered === r.planned_cans ? int(r.entered) : `${int(r.entered)} of ${int(r.planned_cans)}`,
      `${r.caught} of ${r.seeded}`,
      ...d.cameras.map((c) => detectionCell(r.cameras[c.key])),
      warnCell(r.false_rejects),
      r.warnings.length ? h("span", { class: "warn-cell", title: r.warnings.join("\n") },
        h("span", { class: "status-icon" }, "⚠"), r.warnings[0]) : h("span", { class: "muted" }, "–")]),
    "No validation runs in this period. Start one from the operator screen.",
    d.validation.map((r) => `/run/${r.run_id}`)));
}

function showMore(wrap, shown, total, key, noun) {
  if (total <= shown && !state[key]) return;
  wrap.append(h("button", { class: "btn small more", onclick: () => {
    state[key] = !state[key]; render();
  } }, state[key] ? `Show latest ${PREVIEW[key]}` : `Show all ${total} ${noun}`));
}
const PREVIEW = { allRuns: 15, allHealth: 7 };

function renderRuns() {
  const runs = state.data.runs;
  const shown = state.allRuns ? runs : runs.slice(0, PREVIEW.allRuns);
  const wrap = $("#c-runs .table-wrap");
  wrap.replaceChildren(table(
    [{ label: "Run", n: true }, { label: "Started" }, { label: "Kind" }, { label: "Supplier" }, { label: "SKU" },
      { label: "Operator" }, { label: "Cans", n: true }, { label: "Rejected", n: true }, { label: "Reject rate", n: true },
      { label: "Status" }],
    shown.map((r) => [runLink(r.id), when(r.started_at), r.kind, r.supplier || "–", r.sku || "–", r.operator || "–",
      int(r.cans), int(r.rejected), r.cans ? pct(r.rejected / r.cans) : "–", statusCell(r.status)]),
    undefined, shown.map((r) => `/run/${r.id}`)));
  if (runs.length > PREVIEW.allRuns) showMore(wrap, shown.length, runs.length, "allRuns", "runs");
  if (runs.length >= 200) wrap.append(h("p", { class: "sub" }, "Only the latest 200 runs are listed here; the CSV export has every run."));
}

function renderHealth() {
  const d = state.data;
  const rows = state.allHealth ? d.health : d.health.slice(0, PREVIEW.allHealth);
  const wrap = $("#c-health .table-wrap");
  wrap.replaceChildren(table(
    [{ label: "Powered up" }, { label: "Last heard" }, { label: "Cans", n: true },
      { label: "Lost datagrams", n: true }, { label: "Recovered cans", n: true }, { label: "Lost cans", n: true },
      { label: "Cans outside a run", n: true },
      ...d.cameras.map((c) => ({ label: `${c.name} late`, n: true, color: CAMERA_COLOR[c.key] }))],
    rows.map((r) => [when(r.first_seen), when(r.last_seen), int(r.cans), warnCell(r.missed),
      warnCell(r.recovered), warnCell(r.lost), warnCell(r.unassigned),
      ...d.cameras.map((c) => warnCell(r.late[c.key]))]),
    "The Rj-Pi hasn't reported in this period."));
  if (d.health.length > PREVIEW.allHealth) showMore(wrap, rows.length, d.health.length, "allHealth", "power-ups");
}

// --- Wiring ----------------------------------------------------------------------------------------

for (const btn of $("#range").children) {
  btn.addEventListener("click", () => { state.days = Number(btn.dataset.days); load(); });
}
$("#f-supplier").addEventListener("change", (e) => { state.supplier = e.target.value; load(); });
$("#f-sku").addEventListener("change", (e) => { state.sku = e.target.value; load(); });
for (const btn of document.querySelectorAll("[data-table]")) {
  btn.addEventListener("click", () => {
    const id = btn.closest(".card").id;
    state.tables[id] = !state.tables[id];
    if (state.data) renderCharts();
  });
}
$("#c-shipments .more").addEventListener("click", () => { state.allShipments = !state.allShipments; renderCharts(); });

let lastWidth = 0;
new ResizeObserver((entries) => {
  const w = Math.round(entries[0].contentRect.width);
  if (state.data && Math.abs(w - lastWidth) > 4) { lastWidth = w; hideTip(); renderCharts(); }
}).observe($("#main"));

setInterval(() => { if (!document.hidden) load(); }, 60000);
load();
