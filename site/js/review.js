// Place-name review tool: correct the OCR gazetteer by hand.
//
// Run through tools/review_server.py and every change is saved straight to
// site/data/gazetteers/<map-id>.json. Opened from any other host, changes
// are kept in this browser and can be exported as a file.

import { MapViewer } from "./viewer.js";

const OSD = window.OpenSeadragon;
const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat("en");

const KINDS = [
  "place", "ruin", "wadi", "mountain", "tell", "spring", "well", "river", "headland",
  "region", "water", "road", "height", "graticule", "title", "legend", "glossary", "other",
];
const MAX_BOXES = 2500;

const state = {
  map: null,
  doc: null,
  baseline: new Map(), // id -> JSON as loaded from the server, to work out local edits
  byId: new Map(),
  list: [],
  index: 0,
  current: null,
  server: false,
  mode: null, // { type: "merge" | "alt" | "redraw" | "new" }
  rubber: null,
  undo: [],
  saveTimer: null,
};

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

const storageKey = () => `bartholomew-review:${state.map.id}`;

function readLocalEdits() {
  try { return JSON.parse(localStorage.getItem(storageKey())) || {}; } catch { return {}; }
}

function writeLocalEdits() {
  const edits = {};
  for (const e of state.doc.entries) {
    const json = JSON.stringify(e);
    if (state.baseline.get(e.id) !== json) edits[e.id] = e;
  }
  for (const id of state.baseline.keys()) if (!state.byId.has(id)) edits[id] = null;
  try {
    if (Object.keys(edits).length) localStorage.setItem(storageKey(), JSON.stringify(edits));
    else localStorage.removeItem(storageKey());
    return true;
  } catch {
    return false;
  }
}

// One entry per line, matching tools/ocr/build_gazetteer.py, for tidy diffs.
function serialize(doc) {
  const { entries, ...head } = doc;
  const top = JSON.stringify(head, null, 1).slice(0, -2);
  return `${top},\n "entries": [\n${entries.map((e) => JSON.stringify(e)).join(",\n")}\n]}\n`;
}

function setSaveStatus(text, error = false) {
  $("save-status").textContent = text;
  $("save-status").classList.toggle("is-error", error);
}

function scheduleSave() {
  const localOk = writeLocalEdits();
  if (!state.server) {
    setSaveStatus(localOk ? "Saved in this browser; export to keep" : "Could not save in this browser", !localOk);
    return;
  }
  setSaveStatus("Saving…");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    try {
      const response = await fetch(`api/gazetteers/${state.map.id}.json`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: serialize(state.doc),
      });
      if (!response.ok) throw new Error((await response.json()).error || response.statusText);
      setBaseline();
      writeLocalEdits();
      setSaveStatus("Saved");
    } catch (error) {
      setSaveStatus(`Not saved: ${error.message}`, true);
    }
  }, 700);
}

function setBaseline() {
  state.baseline = new Map(state.doc.entries.map((e) => [e.id, JSON.stringify(e)]));
}

// ---------------------------------------------------------------------------
// Entries
// ---------------------------------------------------------------------------

function indexEntries() {
  state.byId = new Map(state.doc.entries.map((e) => [e.id, e]));
}

function remember(...entries) {
  state.undo.push(entries.map((e) => ({ id: e.id, before: e._new ? null : structuredClone(e) })));
  for (const e of entries) delete e._new;
  if (state.undo.length > 200) state.undo.shift();
}

function touched(...entries) {
  for (const e of entries) e.reviewed = true;
  scheduleSave();
  drawBoxes();
  updateProgress();
}

function undo() {
  const step = state.undo.pop();
  if (!step) return;
  for (const { id, before } of step) {
    const i = state.doc.entries.findIndex((e) => e.id === id);
    if (before === null) {
      if (i >= 0) state.doc.entries.splice(i, 1);
      state.list = state.list.filter((x) => x !== id);
    } else if (i >= 0) {
      state.doc.entries[i] = before;
    } else {
      state.doc.entries.push(before);
    }
  }
  indexEntries();
  scheduleSave();
  updateProgress();
  const back = state.byId.get(step[0].id) || state.byId.get(state.list[state.index]);
  if (back) select(back);
}

function nextId() {
  let max = 0;
  for (const e of state.doc.entries) max = Math.max(max, Number(e.id.slice(1)) || 0);
  return `l${String(max + 1).padStart(5, "0")}`;
}

const centre = (e) => ({ x: e.box[0] + e.box[2] / 2, y: e.box[1] + e.box[3] / 2 });

function unionBox(a, b) {
  const x0 = Math.min(a[0], b[0]), y0 = Math.min(a[1], b[1]);
  const x1 = Math.max(a[0] + a[2], b[0] + b[2]), y1 = Math.max(a[1] + a[3], b[1] + b[3]);
  return [x0, y0, x1 - x0, y1 - y0];
}

// ---------------------------------------------------------------------------
// Queue and filters
// ---------------------------------------------------------------------------

function applyFilters(keepCurrent = false) {
  const status = $("filter-status").value;
  const kind = $("filter-kind").value;
  const text = $("filter-text").value.trim().toLowerCase();
  state.list = state.doc.entries
    .filter((e) => status === "all" || e.status === status)
    .filter((e) => !kind || e.kind === kind)
    .filter((e) => !text || e.name.toLowerCase().includes(text) || (e.ocr?.text || "").toLowerCase().includes(text))
    .map((e) => e.id);
  const at = keepCurrent && state.current ? state.list.indexOf(state.current.id) : -1;
  state.index = Math.max(0, at);
  const first = state.byId.get(state.list[state.index]);
  if (first) select(first);
  else showEmpty();
}

function step(delta) {
  if (!state.list.length) return;
  const at = state.current ? state.list.indexOf(state.current.id) : -1;
  const from = at >= 0 ? at : state.index - (delta > 0 ? 1 : 0);
  state.index = Math.min(state.list.length - 1, Math.max(0, from + delta));
  select(state.byId.get(state.list[state.index]));
}

function renderQueue() {
  const queue = $("queue");
  queue.replaceChildren();
  const start = Math.max(0, state.index - 3);
  for (const id of state.list.slice(start, start + 60)) {
    const e = state.byId.get(id);
    if (!e) continue;
    const li = document.createElement("li");
    li.className = `s-${e.status}${e === state.current ? " is-current" : ""}`;
    li.dataset.id = e.id;
    li.innerHTML = '<span class="q-dot"></span><span class="q-name"></span><span class="q-kind"></span>';
    li.querySelector(".q-name").textContent = e.name || "—";
    li.querySelector(".q-kind").textContent = e.kind;
    queue.append(li);
  }
}

function updateProgress() {
  const entries = state.doc.entries;
  const left = entries.filter((e) => e.status === "unreviewed").length;
  const confirmed = entries.filter((e) => e.status === "confirmed").length;
  $("progress").textContent =
    `${number.format(left)} left to review · ${number.format(confirmed)} confirmed · ${number.format(entries.length)} labels`;
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

function select(entry, { zoom = true } = {}) {
  state.current = entry;
  const at = state.list.indexOf(entry.id);
  if (at >= 0) state.index = at;

  $("position").textContent = at >= 0
    ? `${number.format(at + 1)} of ${number.format(state.list.length)}`
    : "Not in this list";
  $("status").textContent = `${entry.status}${entry.into ? ` into ${state.byId.get(entry.into)?.name || entry.into}` : ""}`;
  $("reading-1").textContent = entry.ocr?.text || "—";
  $("reading-2").textContent = entry.ocr?.alt || "";
  $("reading-2-row").hidden = !entry.ocr?.alt;
  $("name").value = entry.name;
  $("alt").value = (entry.alt || []).join("; ");
  $("kind").value = entry.kind;
  $("region").value = entry.region || "";
  $("note").value = entry.note || "";

  if (zoom) zoomTo(entry);
  drawBoxes();
  renderQueue();
  $("name").focus({ preventScroll: true });
}

function showEmpty() {
  state.current = null;
  $("position").textContent = "Nothing matches these filters.";
  $("status").textContent = "";
  for (const id of ["name", "alt", "note"]) $(id).value = "";
  renderQueue();
  drawBoxes();
}

function readEditor(entry) {
  entry.name = $("name").value.trim();
  entry.alt = $("alt").value.split(";").map((s) => s.trim()).filter(Boolean);
  entry.kind = $("kind").value;
  if ($("region").value) entry.region = $("region").value; else delete entry.region;
  if ($("note").value.trim()) entry.note = $("note").value.trim(); else delete entry.note;
}

function zoomTo(entry) {
  const [x, y, w, h] = entry.box;
  const vp = viewer.osd.viewport;
  // Show the label with plenty of surrounding map for context.
  const width = Math.max(w * 3, 700);
  const height = Math.max(h * 6, 450);
  const rect = vp.imageToViewportRectangle(x + w / 2 - width / 2, y + h / 2 - height / 2, width, height);
  vp.fitBoundsWithConstraints(rect);
}

function confirm() {
  const e = state.current;
  if (!e) return;
  remember(e);
  readEditor(e);
  e.status = e.name ? "confirmed" : "rejected";
  delete e.into;
  touched(e);
  step(1);
}

function reject() {
  const e = state.current;
  if (!e) return;
  remember(e);
  readEditor(e);
  e.status = "rejected";
  delete e.into;
  touched(e);
  step(1);
}

// ---------------------------------------------------------------------------
// Modes: join, alternate, redraw, new
// ---------------------------------------------------------------------------

const MODE_TEXT = {
  merge: "Click the box to join to this label · Esc to cancel",
  alt: "Click the main name this is an alternate of · Esc to cancel",
  redraw: "Drag a new box round the label · Esc to cancel",
  new: "Drag a box round the missing label · Esc to cancel",
};

function setMode(type) {
  state.mode = type && (type === "new" || state.current) ? { type } : null;
  $("mode-banner").hidden = !state.mode;
  $("mode-banner").textContent = state.mode ? MODE_TEXT[type] : "";
  for (const [id, t] of [["merge", "merge"], ["make-alt", "alt"], ["redraw", "redraw"], ["new", "new"]]) {
    $(id).setAttribute("aria-pressed", String(state.mode?.type === t));
  }
  state.rubber = null;
  drawBoxes();
}

function entryAt(point) {
  const slop = 4 / Math.max(viewer.getView().zoom, 0.05);
  let best = null;
  for (const e of state.doc.entries) {
    const [x, y, w, h] = e.box;
    if (point.x >= x - slop && point.x <= x + w + slop && point.y >= y - slop && point.y <= y + h + slop) {
      if (!best || w * h < best.box[2] * best.box[3]) best = e;
    }
  }
  return best;
}

function onMapClick(point) {
  const hit = entryAt(point);
  const mode = state.mode?.type;
  if ((mode === "merge" || mode === "alt") && hit && hit !== state.current) {
    const a = state.current;
    remember(a, hit);
    readEditor(a);
    if (mode === "merge") {
      // Reading order: left to right on one line, otherwise top to bottom.
      const sameLine = Math.abs(centre(a).y - centre(hit).y) < Math.max(a.box[3], hit.box[3]) / 2;
      const aFirst = sameLine ? centre(a).x <= centre(hit).x : centre(a).y <= centre(hit).y;
      a.name = (aFirst ? [a.name, hit.name] : [hit.name, a.name]).filter(Boolean).join(" ");
      a.alt = [...new Set([...(a.alt || []), ...(hit.alt || [])])];
      a.box = unionBox(a.box, hit.box);
      hit.status = "merged";
      hit.into = a.id;
      setMode(null);
      touched(a, hit);
      select(a, { zoom: false });
    } else {
      const altName = a.name.replace(/^\(|\)$/g, "").trim();
      if (altName && !hit.alt.includes(altName)) hit.alt.push(altName);
      a.status = "merged";
      a.into = hit.id;
      setMode(null);
      touched(a, hit);
      step(1);
    }
    return;
  }
  if (!mode && hit) select(hit, { zoom: false });
}

function startDrawing(event) {
  if (state.mode?.type !== "redraw" && state.mode?.type !== "new") return;
  state.rubber = { start: viewer.imagePointFromPixel(event.position) };
}

function dragDrawing(event) {
  if (!state.rubber?.start) return;
  event.preventDefaultAction = true; // don't pan while drawing
  state.rubber.end = viewer.imagePointFromPixel(event.position) || state.rubber.end;
  drawBoxes();
}

function finishDrawing() {
  const r = state.rubber;
  if (!r?.start || !r.end) return;
  const box = [
    Math.round(Math.min(r.start.x, r.end.x)), Math.round(Math.min(r.start.y, r.end.y)),
    Math.round(Math.abs(r.end.x - r.start.x)), Math.round(Math.abs(r.end.y - r.start.y)),
  ];
  const type = state.mode.type;
  setMode(null);
  if (box[2] < 3 || box[3] < 3) return;

  if (type === "redraw") {
    remember(state.current);
    readEditor(state.current);
    state.current.box = box;
    touched(state.current);
    select(state.current, { zoom: false });
  } else {
    const entry = { id: nextId(), name: "", alt: [], kind: "place", box, status: "unreviewed", ocr: null, _new: true };
    remember(entry);
    state.doc.entries.push(entry);
    state.byId.set(entry.id, entry);
    state.list.splice(state.index + 1, 0, entry.id);
    touched(entry);
    select(entry, { zoom: false });
  }
}

// ---------------------------------------------------------------------------
// Boxes over the map
// ---------------------------------------------------------------------------

let drawQueued = false;
function drawBoxes() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => {
    drawQueued = false;
    paint();
  });
}

function paint() {
  const svg = $("boxes");
  if (!state.doc || !viewer.osd.world.getItemCount()) { svg.innerHTML = ""; return; }
  const vp = viewer.osd.viewport;
  const origin = vp.imageToViewerElementCoordinates(new OSD.Point(0, 0));
  const scale = vp.imageToViewerElementCoordinates(new OSD.Point(1000, 0)).x / 1000 - origin.x / 1000;
  const view = vp.viewportToImageRectangle(vp.getBounds(true));

  const visible = state.doc.entries.filter((e) => {
    const [x, y, w, h] = e.box;
    return x + w >= view.x && x <= view.x + view.width && y + h >= view.y && y <= view.y + view.height;
  });
  const shown = visible.length > MAX_BOXES ? visible.filter((e) => e === state.current) : visible;

  const rect = (box, cls) => {
    const [x, y, w, h] = box;
    return `<rect class="${cls}" x="${(origin.x + x * scale).toFixed(1)}" y="${(origin.y + y * scale).toFixed(1)}" ` +
      `width="${Math.max(2, w * scale).toFixed(1)}" height="${Math.max(2, h * scale).toFixed(1)}"/>`;
  };
  let html = shown.filter((e) => e !== state.current).map((e) => rect(e.box, `s-${e.status}`)).join("");
  if (state.current) html += rect(state.current.box, "is-current");
  const r = state.rubber;
  if (r?.start && r.end) {
    html += rect([Math.min(r.start.x, r.end.x), Math.min(r.start.y, r.end.y),
      Math.abs(r.end.x - r.start.x), Math.abs(r.end.y - r.start.y)], "rubber");
  }
  svg.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Import and export
// ---------------------------------------------------------------------------

function exportFile() {
  const blob = new Blob([serialize(state.doc)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${state.map.id}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function importFile(file) {
  try {
    const doc = JSON.parse(await file.text());
    if (doc.map !== state.map.id || !Array.isArray(doc.entries)) throw new Error(`This file is not a gazetteer for ${state.map.id}.`);
    state.doc = doc;
    indexEntries();
    state.undo = [];
    scheduleSave();
    updateProgress();
    applyFilters();
  } catch (error) {
    setSaveStatus(error.message, true);
  }
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

const viewer = new MapViewer($("viewer"), { onSelect: onMapClick, onViewChange: drawBoxes });
viewer.osd.addHandler("update-viewport", drawBoxes);
viewer.osd.addHandler("canvas-press", startDrawing);
viewer.osd.addHandler("canvas-drag", dragDrawing);
viewer.osd.addHandler("canvas-release", finishDrawing);

$("editor").addEventListener("submit", (e) => { e.preventDefault(); confirm(); });
$("reject").addEventListener("click", reject);
$("next").addEventListener("click", () => step(1));
$("prev").addEventListener("click", () => step(-1));
$("merge").addEventListener("click", () => setMode(state.mode?.type === "merge" ? null : "merge"));
$("make-alt").addEventListener("click", () => setMode(state.mode?.type === "alt" ? null : "alt"));
$("redraw").addEventListener("click", () => setMode(state.mode?.type === "redraw" ? null : "redraw"));
$("new").addEventListener("click", () => setMode(state.mode?.type === "new" ? null : "new"));
$("undo").addEventListener("click", undo);
$("export").addEventListener("click", exportFile);
$("import").addEventListener("change", (e) => e.target.files[0] && importFile(e.target.files[0]));
$("help-button").addEventListener("click", () => $("help").showModal());
for (const id of ["reading-1", "reading-2"]) {
  $(id).addEventListener("click", () => { $("name").value = $(id).textContent; $("name").focus(); });
}
for (const id of ["filter-status", "filter-kind"]) $(id).addEventListener("change", () => applyFilters());
$("filter-text").addEventListener("input", () => applyFilters());
$("queue").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) select(state.byId.get(li.dataset.id));
});

document.addEventListener("keydown", (e) => {
  if ($("help").open) return;
  const typing = e.target.matches("input, select, textarea");
  if (e.key === "Escape") {
    if (state.mode) setMode(null); else e.target.blur?.();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !typing) {
    e.preventDefault();
    undo();
    return;
  }
  if (e.key === "Enter" && !typing && state.current) {
    e.preventDefault();
    confirm();
    return;
  }
  if (!e.altKey) return;
  const actions = {
    KeyX: reject,
    ArrowRight: () => step(1),
    ArrowLeft: () => step(-1),
    KeyM: () => setMode("merge"),
    KeyA: () => setMode("alt"),
    KeyB: () => setMode("redraw"),
    KeyN: () => setMode("new"),
  };
  if (actions[e.code]) {
    e.preventDefault();
    actions[e.code]();
  }
});

window.addEventListener("beforeunload", (e) => {
  if (state.server && $("save-status").textContent === "Saving…") e.preventDefault();
});

const registry = await (await fetch("data/maps.json", { cache: "no-store" })).json();
const reviewable = registry.maps.filter((m) => m.gazetteer);
const requested = new URLSearchParams(location.search).get("map");
state.map = reviewable.find((m) => m.id === requested) || reviewable.find((m) => m.id === registry.default) || reviewable[0];

for (const m of reviewable) $("map-select").add(new Option(m.shortTitle, m.id, false, m === state.map));
$("map-select").addEventListener("change", (e) => { location.search = `?map=${e.target.value}`; });

if (!state.map) {
  $("progress").textContent = "No map has a gazetteer yet. Run tools/ocr/build_gazetteer.py first.";
} else {
  state.server = await fetch("api/ping", { cache: "no-store" }).then((r) => r.ok, () => false);
  const [doc] = await Promise.all([
    fetch(state.map.gazetteer, { cache: "no-store" }).then((r) => r.json()),
    viewer.open(state.map.image),
  ]);
  state.doc = doc;
  setBaseline();

  // Re-apply changes kept in this browser and not yet saved to the file.
  const edits = readLocalEdits();
  for (const [id, entry] of Object.entries(edits)) {
    const i = doc.entries.findIndex((e) => e.id === id);
    if (entry === null) { if (i >= 0) doc.entries.splice(i, 1); }
    else if (i >= 0) doc.entries[i] = entry;
    else doc.entries.push(entry);
  }
  indexEntries();

  const kinds = new Set([...KINDS, ...doc.entries.map((e) => e.kind)]);
  for (const k of kinds) {
    $("kind").add(new Option(k, k));
    $("filter-kind").add(new Option(k, k));
  }
  $("region").add(new Option("Main map", ""));
  for (const [id, label] of Object.entries(doc.regions || {})) $("region").add(new Option(label, id));

  if (Object.keys(edits).length) {
    if (state.server) scheduleSave();
    else setSaveStatus(`${number.format(Object.keys(edits).length)} changes kept in this browser; export to keep`);
  } else {
    setSaveStatus(state.server ? "Saving to the repository" : "Changes will be kept in this browser");
  }
  updateProgress();
  applyFilters();
}
