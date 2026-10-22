// Place-name search with autocomplete.
//
// Each map may name a gazetteer (site/data/gazetteers/<map-id>.json): the
// labels printed on it, read by OCR and corrected in the review tool,
//
//   { "regions": { "jerusalem": "Jerusalem inset", ... },
//     "entries": [{ "name": "Beth-shean", "alt": ["Beisan"], "kind": "place",
//                   "box": [x, y, width, height], "status": "confirmed" }, ...] }
//
// where "box" is the label's position in image pixels. Switching maps
// switches gazetteers.

const MAX_RESULTS = 12;

// Labels that are not places to search for: spot heights, the degree
// figures in the margin, the title, legends and the glossary of terms.
const UNSEARCHABLE_KINDS = new Set(["height", "graticule", "title", "legend", "glossary"]);
const SEARCHABLE_STATUS = new Set(["confirmed", "unreviewed"]);

// Fold case, accents, and the punctuation that varies between transliterations
// ("Beth-she'an", "Beth Shean", "Bethshean"), so they all match one another.
export function normalize(text) {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[''`ʻʼ‘’.\-]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Levenshtein distance, giving up early once it exceeds `limit`.
function editDistance(a, b, limit) {
  if (Math.abs(a.length - b.length) > limit) return limit + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      rowMin = Math.min(rowMin, cur[j]);
    }
    if (rowMin > limit) return limit + 1;
    prev = cur;
  }
  return prev[b.length];
}

// Lower is better; null means no match.
function score(query, key) {
  const compact = key.replace(/ /g, "");
  const q = query.replace(/ /g, "");
  if (compact.startsWith(q)) return 0;
  if (key.split(" ").some((word) => word.startsWith(query))) return 1;
  if (compact.includes(q)) return 2;
  if (q.length >= 4) {
    const limit = q.length >= 7 ? 2 : 1;
    const d = editDistance(q, compact.slice(0, q.length + limit), limit);
    if (d <= limit) return 3 + d;
  }
  return null;
}

export function searchEntries(entries, query) {
  const q = normalize(query);
  if (!q) return [];
  const hits = [];
  for (const entry of entries) {
    let best = null;
    let matched = entry.name;
    for (const name of [entry.name, ...(entry.alt || [])]) {
      const s = score(q, normalize(name));
      if (s !== null && (best === null || s < best)) { best = s; matched = name; }
    }
    if (best !== null) hits.push({ entry, matched, score: best });
  }
  const unconfirmed = (hit) => (hit.entry.status === "confirmed" ? 0 : 1);
  hits.sort((a, b) =>
    a.score - b.score || unconfirmed(a) - unconfirmed(b) || a.entry.name.localeCompare(b.entry.name));
  return hits.slice(0, MAX_RESULTS);
}

export class PlaceSearch {
  constructor(form, { onChoose }) {
    this.form = form;
    this.input = form.querySelector("input");
    this.list = form.querySelector("[role=listbox]");
    this.onChoose = onChoose;
    this.entries = null;
    this.status = "none";
    this.results = [];
    this.active = -1;

    this.input.addEventListener("input", () => this.update());
    this.input.addEventListener("focus", () => this.update());
    this.input.addEventListener("keydown", (e) => this.keydown(e));
    this.input.addEventListener("blur", () => setTimeout(() => this.close(), 150));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      this.choose(this.active >= 0 ? this.active : 0);
    });
    this.list.addEventListener("mousedown", (e) => e.preventDefault());
    this.list.addEventListener("click", (e) => {
      const li = e.target.closest("li[data-index]");
      if (li) this.choose(Number(li.dataset.index));
    });
  }

  async setGazetteer(url) {
    this.entries = null;
    this.status = url ? "loading" : "none";
    this.input.value = "";
    this.close();
    if (!url) return;
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(response.statusText);
      const doc = await response.json();
      const regions = doc.regions || {};
      this.entries = doc.entries
        .filter((e) => e.name && SEARCHABLE_STATUS.has(e.status) && !UNSEARCHABLE_KINDS.has(e.kind))
        .map((e) => ({ ...e, regionLabel: regions[e.region] }));
      this.status = "ready";
    } catch {
      this.status = "error";
    }
    if (document.activeElement === this.input) this.update();
  }

  update() {
    const query = this.input.value;
    if (!query.trim()) return this.close();

    if (this.status !== "ready") {
      const notes = {
        none: "The place-name index for this map is still being compiled.",
        loading: "Loading place names…",
        error: "The place-name index could not be loaded.",
      };
      return this.render([], notes[this.status]);
    }
    this.results = searchEntries(this.entries, query);
    this.render(this.results, this.results.length ? null : "No matching place names.");
  }

  render(results, note) {
    this.active = -1;
    this.list.replaceChildren();
    if (note) {
      const li = document.createElement("li");
      li.className = "search__note";
      li.textContent = note;
      this.list.append(li);
    }
    results.forEach(({ entry, matched }, i) => {
      const li = document.createElement("li");
      li.id = `search-option-${i}`;
      li.setAttribute("role", "option");
      li.dataset.index = i;
      li.textContent = entry.name;
      if (matched !== entry.name) {
        const alt = document.createElement("span");
        alt.className = "search__alt";
        alt.textContent = ` (${matched})`;
        li.append(alt);
      }
      if (entry.regionLabel) {
        const region = document.createElement("span");
        region.className = "search__region";
        region.textContent = entry.regionLabel;
        li.append(region);
      }
      this.list.append(li);
    });
    this.list.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  keydown(e) {
    if (e.key === "Escape") { this.close(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (!this.results.length) return;
    e.preventDefault();
    const n = this.results.length;
    this.active = (this.active + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
    for (const li of this.list.querySelectorAll("[role=option]")) {
      li.setAttribute("aria-selected", String(Number(li.dataset.index) === this.active));
    }
    this.input.setAttribute("aria-activedescendant", `search-option-${this.active}`);
    this.list.querySelector(`#search-option-${this.active}`)?.scrollIntoView({ block: "nearest" });
  }

  choose(index) {
    const hit = this.results[index];
    if (!hit) return;
    this.input.value = hit.entry.name;
    this.close();
    this.input.blur();
    this.onChoose(hit.entry);
  }

  close() {
    this.list.hidden = true;
    this.results = this.status === "ready" ? this.results : [];
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
  }
}
