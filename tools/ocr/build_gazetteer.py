"""
Turn raw OCR words into a gazetteer of map labels, ready for review.

    python3 tools/ocr/build_gazetteer.py palestine-1901

Reads  ocr/<map-id>/words.json   (from run_ocr.py)
       ocr/<map-id>/config.json  (optional: insets, margins and other regions)
Writes site/data/gazetteers/<map-id>.json

Words are joined into labels when they share a baseline, size and angle.
A parenthesised label just below another (the modern Arabic name under a
Biblical one, as in "Beth-shean / (Beisan)") becomes that label's alternate
name. Each label gets a provisional kind (place, ruin, wadi, height, ...).

Rerunning is safe: entries already touched in the review tool
("reviewed": true) are kept exactly as they are, and only fresh OCR labels
that don't overlap them are added.
"""

import argparse
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

KIND_PREFIXES = [
    (r"(W|Wady|Wadi)\b\.?", "wadi"),
    (r"(Kh|Khurbet|Khirbet)\b\.?", "ruin"),
    (r"(Jebel|Jeb|J|Mt|Mount)\b\.?", "mountain"),
    (r"(Tell|Tel|T)\b\.?", "tell"),
    (r"(Ain|ʿAin|'Ain|Ayun)\b", "spring"),
    (r"(Bir|Biar)\b", "well"),
    (r"(Nahr|R|River)\b\.?", "river"),
    (r"(Ras)\b", "headland"),
]
HEIGHT = re.compile(r"^[-−]?\d{2,5}$")
# Arabic articles and particles: alone they are pieces of a nearby name, to
# be joined in review rather than discarded.
PARTICLES = {"el", "en", "es", "ed", "et", "er", "esh", "ez", "eth", "ej", "ash", "ibn", "abu", "umm", "of", "or"}


def is_fragment(name):
    """Stray letters and noise: a lone letter, or a short lowercase scrap."""
    letters = re.sub(r"[^A-Za-z]", "", name)
    if re.search(r"\d", name):
        return False
    if len(letters) <= 1:
        return True
    return len(letters) <= 3 and letters.islower() and letters not in PARTICLES


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def describe(poly):
    """Centre, unit direction, length and height of a word's box.

    The OCR polygon runs top-left, top-right, bottom-right, bottom-left in
    the word's own reading direction, so the first edge gives its angle
    (even for a single letter, whose box may be taller than it is wide)."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = poly
    cx, cy = (x0 + x1 + x2 + x3) / 4, (y0 + y1 + y2 + y3) / 4
    length = max(math.hypot(x1 - x0, y1 - y0), 1)
    height = math.hypot(x3 - x0, y3 - y0)
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    if ux < 0 or (ux == 0 and uy < 0):  # point left to right
        ux, uy = -ux, -uy
    return {"cx": cx, "cy": cy, "ux": ux, "uy": uy, "len": length, "h": max(height, 1)}


SPACED_CAPITAL = re.compile(r"^[A-Z]$")


def lettering(text):
    """Capitals and lower-case lettering mark different kinds of feature on
    the map ("BEIRUT" beside "Ras Beirut"), so they never form one label."""
    letters = re.sub(r"[^A-Za-z]", "", text)
    if len(letters) < 3:
        return None
    return "caps" if letters.isupper() else "mixed"


def same_label(a, b):
    if bool(HEIGHT.match(a["text"])) != bool(HEIGHT.match(b["text"])):
        return False
    la, lb = lettering(a["text"]), lettering(b["text"])
    if la and lb and la != lb:
        return False
    if abs(a["ux"] * b["uy"] - a["uy"] * b["ux"]) > math.sin(math.radians(12)):
        return False
    h = (a["h"] + b["h"]) / 2
    if max(a["h"], b["h"]) / min(a["h"], b["h"]) > 1.5:
        return False
    dx, dy = b["cx"] - a["cx"], b["cy"] - a["cy"]
    across = abs(-dx * a["uy"] + dy * a["ux"])
    along = abs(dx * a["ux"] + dy * a["uy"])
    gap = along - (a["len"] + b["len"]) / 2
    if across >= 0.45 * h or gap <= -0.3 * h:
        return False
    if gap < 1.3 * h:
        return True
    # Region names are lettered with wide spacing: "G I L E A D".
    if SPACED_CAPITAL.match(a["text"]) and SPACED_CAPITAL.match(b["text"]):
        return gap < 6 * h
    # A second engine saw both words as one label ("Wady" + "Taid").
    shared = a["lines"] & b["lines"]
    return bool(shared) and gap < 3 * h


def box_of(polys):
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    return [round(min(xs)), round(min(ys)), round(max(xs) - min(xs)), round(max(ys) - min(ys))]


def overlap(a, b):
    """Intersection of two [x, y, w, h] boxes as a fraction of the smaller."""
    iw = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    ih = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    return iw * ih / max(1, min(a[2] * a[3], b[2] * b[3]))


def contains(outer, inner, fraction=0.6):
    """Whether most of box `inner` lies within box `outer`."""
    iw = min(outer[0] + outer[2], inner[0] + inner[2]) - max(outer[0], inner[0])
    ih = min(outer[1] + outer[3], inner[1] + inner[3]) - max(outer[1], inner[1])
    return iw > 0 and ih > 0 and iw * ih >= fraction * max(1, inner[2] * inner[3])


def inside(box, rect):
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def clean(text):
    text = re.sub(r"\s+", " ", text).strip()
    # Settlement circles and dots read as a leading "o" or "." ("oKefr Meya").
    text = re.sub(r"^[oO0°.,·]+(?=[A-Z])", "", text)
    text = re.sub(r"[.,:;·]+$", "", text)
    return text


# Small words kept lower case inside a name, as the map prints them:
# Arabic articles and particles, and English connectives.
LOWER_WORDS = PARTICLES | {"of", "or", "the", "and", "to", "from", "in", "at", "on", "as", "an", "a", "by", "de", "du", "la"}


def title_case(text):
    """Turn words lettered in capitals into title case ("SEA OF GALILEE" ->
    "Sea of Galilee", "EL KUDS (JERUSALEM)" -> "El Kuds (Jerusalem)").
    Words already in mixed or lower case are left exactly as printed."""
    def fix(match):
        word = match.group(0)
        if len(re.sub(r"[\W\d_]", "", word)) < 2 or not word.isupper():
            return word
        lower = word.lower()
        if match.start() > 0 and lower in LOWER_WORDS:
            return lower
        return "-".join(part[:1].upper() + part[1:] for part in lower.split("-"))
    return re.sub(r"[^\W\d_][^\W\d_'’\-]*(?:['’\-][^\W\d_]+)*", fix, text)


def kind_of(name):
    if HEIGHT.match(name.replace(",", "")):
        return "height"
    for pattern, kind in KIND_PREFIXES:
        if re.match(pattern, name):
            return kind
    return "place"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def group_words(words, lines):
    line_grid = {}
    for i, line in enumerate(lines):
        x, y, w, h = line["box"]
        for gx in range(int(x // 256), int((x + w) // 256) + 1):
            for gy in range(int(y // 256), int((y + h) // 256) + 1):
                line_grid.setdefault((gx, gy), []).append(i)
    for w in words:
        w.update(describe(w["poly"]))
        box = box_of([w["poly"]])
        nearby = line_grid.get((int(w["cx"] // 256), int(w["cy"] // 256)), [])
        w["lines"] = {i for i in nearby if contains(lines[i]["box"], box)}

    parent = list(range(len(words)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Compare each word only with words in nearby grid cells.
    cell = 128
    grid = {}
    for i, w in enumerate(words):
        grid.setdefault((int(w["cx"] // cell), int(w["cy"] // cell)), []).append(i)
    for i, w in enumerate(words):
        gx, gy = int(w["cx"] // cell), int(w["cy"] // cell)
        reach = 1 + int((w["len"] + 6 * w["h"]) // cell)
        for x in range(gx - reach, gx + reach + 1):
            for y in range(gy - 1, gy + 2):
                for j in grid.get((x, y), []):
                    if j > i and same_label(w, words[j]):
                        parent[find(j)] = find(i)

    groups = {}
    for i in range(len(words)):
        groups.setdefault(find(i), []).append(words[i])

    labels = []
    for members in groups.values():
        ux = sum(m["ux"] for m in members) / len(members)
        uy = sum(m["uy"] for m in members) / len(members)
        members.sort(key=lambda m: m["cx"] * ux + m["cy"] * uy)
        if len(members) > 2 and all(SPACED_CAPITAL.match(m["text"]) for m in members):
            text = "".join(m["text"] for m in members)
        else:
            text = " ".join(m["text"] for m in members)
        angle = math.degrees(math.atan2(uy, ux))
        labels.append({
            "text": text,
            "conf": min(m["conf"] for m in members),
            "box": box_of([m["poly"] for m in members]),
            "angle": round(angle, 1) if abs(angle) >= 3 else 0,
            "h": sum(m["h"] for m in members) / len(members),
        })
    return labels


def second_reading(label, lines):
    """The RapidOCR reading of the same spot, if it differs."""
    hits = [l for l in lines if overlap(label["box"], l["box"]) > 0.5]
    hits.sort(key=lambda l: (l["box"][1] // max(1, label["box"][3]), l["box"][0]))
    text = clean(" ".join(l["text"] for l in hits))
    return text if text and text.replace(" ", "") != label["name"].replace(" ", "") else None


def link_alternates(entries):
    """Attach "(Beisan)" under "Beth-shean" as an alternate name."""
    for child in entries:
        if not child["ocr"]["text"].lstrip(" .o").startswith("(") or child["status"] != "unreviewed":
            continue
        cx, cy = child["box"][0] + child["box"][2] / 2, child["box"][1] + child["box"][3] / 2
        best, best_d = None, None
        for p in entries:
            if p is child or p["status"] != "unreviewed" or p["kind"] == "height" or p["name"].startswith("("):
                continue
            px, py = p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2
            h = p["box"][3]
            horizontal = min(p["box"][0] + p["box"][2], child["box"][0] + child["box"][2]) - max(p["box"][0], child["box"][0])
            if 0.3 * h < cy - py < 2.6 * h and horizontal > 0:
                d = math.hypot(cx - px, cy - py)
                if best_d is None or d < best_d:
                    best, best_d = p, d
        if best:
            alt = child["name"].strip("() ")
            if alt:
                best["alt"].append(alt)
            child["status"] = "merged"
            child["into"] = best["id"]


def unsearchable_regions(config):
    """Regions whose labels stay in the gazetteer but not in the site's search
    (set "searchable": false on the region in config.json)."""
    return [r["id"] for r in config.get("regions", []) if r.get("searchable") is False]


def region_for(box, config):
    neat = config.get("neatline")
    if neat and not inside(box, neat):
        return config.get("outsideNeatline")
    return next((r for r in config.get("regions", []) if inside(box, r["rect"])), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("map_id")
    args = parser.parse_args()

    ocr = json.loads((ROOT / "ocr" / args.map_id / "words.json").read_text())
    config_path = ROOT / "ocr" / args.map_id / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    out = ROOT / "site" / "data" / "gazetteers" / f"{args.map_id}.json"

    kept = []
    if out.exists():
        kept = [e for e in json.loads(out.read_text())["entries"] if e.get("reviewed")]
    next_id = 1 + max((int(e["id"][1:]) for e in kept), default=0)

    lines = [{"text": l["text"], "box": box_of([l["poly"]])} for l in ocr["lines"]]
    labels = group_words(ocr["words"], lines)
    labels.sort(key=lambda l: (l["box"][1] // 200, l["box"][0]))

    fresh = []
    for label in labels:
        name = clean(label["text"])
        if not re.search(r"[A-Za-z0-9]", name):
            continue
        if any(overlap(label["box"], k["box"]) > 0.3 for k in kept):
            continue
        entry = {
            "id": f"l{next_id:05d}",
            "name": title_case(name),
            "alt": [],
            "kind": kind_of(name),
            "box": label["box"],
            "status": "unreviewed",
            "ocr": {"text": label["text"], "conf": label["conf"]},
        }
        next_id += 1
        if label["angle"]:
            entry["angle"] = label["angle"]
        reading = second_reading({**label, "name": name}, lines)
        if reading:
            entry["ocr"]["alt"] = reading

        neat = config.get("neatline")
        if neat and not inside(entry["box"], neat):
            region = config["outsideNeatline"]
        else:
            region = next((r for r in config.get("regions", []) if inside(entry["box"], r["rect"])), None)
        if entry["kind"] != "height" and is_fragment(name):
            entry["status"] = "rejected"
            entry["note"] = "Rejected automatically as a fragment"
        if region:
            entry["region"] = region["id"]
            entry["kind"] = region.get("kind", entry["kind"])
            entry["status"] = region.get("status", entry["status"])
        fresh.append(entry)

    link_alternates(fresh)

    entries = sorted(kept + fresh, key=lambda e: e["id"])
    regions = {r["id"]: r["label"] for r in config.get("regions", [])}
    if "outsideNeatline" in config:
        regions[config["outsideNeatline"]["id"]] = config["outsideNeatline"]["label"]
    document = {
        "map": args.map_id,
        "image": ocr["image"],
        "source": ocr["source"],
        "regions": regions,
        "unsearchableRegions": unsearchable_regions(config),
        "entries": entries,
    }
    write_gazetteer(out, document)

    counts = {}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    print(f"Wrote {len(entries)} entries to {out} ({len(kept)} reviewed kept): {counts}")


def write_gazetteer(path, document):
    """One entry per line, so reviews make readable git diffs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = {k: v for k, v in document.items() if k != "entries"}
    body = ",\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in document["entries"])
    text = json.dumps(head, ensure_ascii=False, indent=1)[:-2] + ',\n "entries": [\n' + body + "\n]}\n"
    path.write_text(text)


if __name__ == "__main__":
    main()
