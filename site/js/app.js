import { MapViewer } from "./viewer.js";
import { PlaceSearch } from "./search.js";

const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat("en");

const state = { registry: null, map: null };

// ---------------------------------------------------------------------------
// URL hash: #map=<id>&at=<x>,<y>,<zoom> makes any view shareable.
// ---------------------------------------------------------------------------

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const at = (params.get("at") || "").split(",").map(Number);
  return {
    mapId: params.get("map"),
    view: at.length === 3 && at.every(Number.isFinite) ? { x: at[0], y: at[1], zoom: at[2] } : null,
  };
}

function writeHash(view) {
  if (!state.map) return;
  const at = [Math.round(view.x), Math.round(view.y), view.zoom.toFixed(4)].join(",");
  history.replaceState(null, "", `#map=${state.map.id}&at=${at}`);
}

// ---------------------------------------------------------------------------
// Information bar
// ---------------------------------------------------------------------------

function showMessage(text) {
  $("infobar-message").textContent = text;
}

function showZoom(view) {
  $("zoom-level").textContent = `${Math.round(view.zoom * 100)}%`;
  $("zoom-level").title = "Magnification relative to the scan's native resolution";
}

function selectPoint(point) {
  viewer.showPin(point);
  // Latitude, longitude and elevation need the map's georeference (ground
  // control points tying sheet pixels to coordinates) and an elevation
  // source. Until a map has one, report the position on the sheet instead.
  if (!state.map.georeference) {
    $("readout-lat").textContent = "—";
    $("readout-lon").textContent = "—";
    $("readout-elev").textContent = "—";
    showMessage(
      `Point at ${number.format(Math.round(point.x))} × ${number.format(Math.round(point.y))} px on the sheet. ` +
      "Coordinates will appear once this map is georeferenced."
    );
  }
}

// ---------------------------------------------------------------------------
// Maps
// ---------------------------------------------------------------------------

async function openMap(map, view = null) {
  state.map = map;
  document.title = `${map.shortTitle} · Bartholomew`;
  $("map-title").textContent = map.title;
  $("map-short-title").textContent = map.shortTitle;
  $("map-switcher").title = `${map.title} (${map.date})`;
  $("scan-credit").textContent = map.scan.name;
  $("scan-credit").href = map.scan.url;
  for (const li of $("map-list").children) {
    li.setAttribute("aria-selected", String(li.dataset.id === map.id));
  }
  showMessage("Tap or click the map to select a point.");
  search.setGazetteer(map.gazetteer);

  try {
    await viewer.open(map.image, view);
  } catch (error) {
    const note = document.createElement("div");
    note.className = "stage__error";
    note.textContent = `The map could not be loaded. ${error.message}`;
    document.querySelector(".stage").append(note);
    throw error;
  }
}

function buildMapList() {
  const list = $("map-list");
  const button = $("map-switcher");
  const { maps } = state.registry;

  for (const map of maps) {
    const li = document.createElement("li");
    li.setAttribute("role", "option");
    li.dataset.id = map.id;
    li.tabIndex = -1;
    const thumb = map.image.replace(/info\.json$/, "thumbnail.jpg");
    li.innerHTML = `<img alt="" loading="lazy" src="${thumb}">
      <span><span class="map-list__title"></span><span class="map-list__meta"></span></span>`;
    li.querySelector(".map-list__title").textContent = map.title;
    li.querySelector(".map-list__meta").textContent = `${map.date} · ${map.credit}`;
    list.append(li);
  }

  const setOpen = (open) => {
    list.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) (list.querySelector('[aria-selected="true"]') || list.firstElementChild)?.focus();
  };

  button.addEventListener("click", () => setOpen(list.hidden));
  list.addEventListener("click", (e) => {
    const li = e.target.closest("li");
    if (!li) return;
    setOpen(false);
    const map = maps.find((m) => m.id === li.dataset.id);
    if (map && map !== state.map) openMap(map);
  });
  list.addEventListener("keydown", (e) => {
    const items = [...list.children];
    const i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") items[(i + 1) % items.length].focus();
    else if (e.key === "ArrowUp") items[(i - 1 + items.length) % items.length].focus();
    else if (e.key === "Enter" || e.key === " ") document.activeElement.click();
    else if (e.key === "Escape") { setOpen(false); button.focus(); }
    else return;
    e.preventDefault();
  });
  document.addEventListener("click", (e) => {
    if (!list.hidden && !list.contains(e.target) && !button.contains(e.target)) setOpen(false);
  });
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

const viewer = new MapViewer($("viewer"), {
  onSelect: selectPoint,
  onZoom: showZoom,
  onViewChange: writeHash,
});

const search = new PlaceSearch($("search"), {
  onChoose: (entry) => {
    const [x, y, width, height] = entry.box;
    viewer.highlight({ x, y, width, height });
    showMessage(entry.name);
  },
});

$("zoom-in").addEventListener("click", () => viewer.zoomBy(1.6));
$("zoom-out").addEventListener("click", () => viewer.zoomBy(1 / 1.6));
$("zoom-home").addEventListener("click", () => viewer.goHome());

if (document.fullscreenEnabled) {
  $("fullscreen").hidden = false;
  $("fullscreen").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen();
  });
}

state.registry = await (await fetch("data/maps.json")).json();
buildMapList();

const { mapId, view } = readHash();
const initial =
  state.registry.maps.find((m) => m.id === mapId) ||
  state.registry.maps.find((m) => m.id === state.registry.default);
await openMap(initial, mapId === initial.id ? view : null);
