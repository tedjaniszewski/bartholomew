"""
AI correction pass over a map's OCR gazetteer, using Claude's vision.

The sheet is cut into tiles. For each tile Claude sees the map twice at 2x
(clean, and with every OCR label boxed and numbered) plus the OCR readings,
and returns an exact transcription of each label, flags fragments,
alternate names and non-text, and lists labels the OCR missed. The results
go into the gazetteer as suggestions: entries stay "unreviewed", with the
AI reading in the name field and its confidence and notes alongside, for
you to confirm in the review tool. Nothing you have already reviewed is
touched.

Commands (run from the repository root):

    python3 tools/ocr/ai_correct.py preview palestine-1901   # images only, no API calls
    python3 tools/ocr/ai_correct.py trial   palestine-1901   # 3 tiles, live, prints cost estimate
    python3 tools/ocr/ai_correct.py submit  palestine-1901   # whole map via the Batches API
    python3 tools/ocr/ai_correct.py collect palestine-1901   # when finished: download and apply
    python3 tools/ocr/ai_correct.py apply   palestine-1901   # re-apply downloaded results

Requires: pip install anthropic pillow, and an API key in ANTHROPIC_API_KEY.
"""

import argparse
import base64
import io
import json
import math
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "ocr"))
from make_iiif_tiles import load_image  # noqa: E402
from build_gazetteer import KIND_PREFIXES, overlap, write_gazetteer  # noqa: E402,F401

MODEL = "claude-opus-5-5"
# Approximate prices per million tokens (Claude Opus 5.5); batches are half.
PRICE_IN, PRICE_OUT = 4.00, 20.00

TILE = 512          # sheet pixels per tile; busy tiles are split into quarters
MAX_LABELS = 40     # ...until each holds at most this many labels
PAD = 96            # context around the tile and its labels
MAX_GROW = 160      # how far long labels may stretch a crop beyond its tile
MAX_IMAGE = 1568    # longest side sent to the model
BATCH_BYTES = 180_000_000  # stay well under the 256 MB batch limit

KINDS = ["place", "ruin", "wadi", "mountain", "tell", "spring", "well", "river", "headland",
         "region", "water", "road", "height", "other"]
# Labels that are not worth a model call: margins, title, legends, glossary.
SKIP_KINDS = {"graticule", "title", "legend", "glossary"}

SYSTEM = """\
You are transcribing the lettering on a scanned map: J. G. Bartholomew's \
"Topographical and Physical Map of Palestine" (Edinburgh, 1901), edited by \
George Adam Smith and compiled from the Palestine Exploration Fund surveys. \
The insets include a plan of Jerusalem, a small map of modern Palestine and \
a map of London.

Conventions on this map:
- Biblical and classical names (Beth-shean, Jabesh Gilead, Scythopolis) and \
transliterated Arabic names (Kefr Kenna, Beit Iksa, Tell el Husn).
- A modern Arabic name is often printed in parentheses under an ancient one: \
"Beth-shean" with "(Beisan)" below.
- A question mark after a name marks an uncertain identification \
("Abel-Meholah?"). Keep it.
- Common abbreviations: Kh. (Khurbet, ruin), W. (Wady), J. or Jeb. (Jebel), \
Mt., R. (river), Sta. (railway station), N. (Nahr). Keep them as printed.
- Bare numbers are spot heights in feet; a minus sign means below sea level.
- Upright capitals name towns, regions and seas; italic lower case names \
villages, ruins and natural features. Region names are often widely letter-spaced.

Transcribe exactly what is printed: the map's own spelling, capitalisation, \
hyphens, apostrophes, periods, accents and question marks. Do not modernise, \
correct or expand anything. Write letter-spaced names without the spaces \
("GILEAD", not "G I L E A D"). If the lettering is genuinely illegible, give \
your best reading and set confidence to "low"."""

INSTRUCTIONS = """\
Image 1 is a crop of the map, enlarged {scale:g}x. Image 2 is the same crop \
with each OCR-detected label outlined in magenta and numbered; the dashed \
cyan rectangle is the part of the crop this request covers.

OCR readings for the numbered boxes (OCR is often wrong; read the image):
{readings}

For every numbered box, return one entry in "labels":
- action "label": the box holds a label (or most of one). "text" is the full \
label exactly as printed. If the box clearly covers two separate labels, give \
the main one and explain in "note".
- action "part_of": the box is only a piece of the label in box "target" \
(e.g. "Kefr" and "Kenna" boxed separately). Give that full label's text in \
the target's own entry; here "text" is just this piece.
- action "alternate_of": a parenthesised alternative name belonging to the \
label in box "target". "text" without the parentheses.
- action "not_text": no lettering here (linework, a symbol, a stain).
"target" is null unless the action is part_of or alternate_of. "kind" is the \
label's kind ("height" for spot heights). "note" is empty unless something \
needs a human's attention.

In "missed", list any label whose centre lies inside the cyan rectangle but \
has no box at all, with its text, kind and a box [x0, y0, x1, y1] in Image 2 \
pixel coordinates. Do not list labels that are boxed, even partly."""

SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "action": {"type": "string", "enum": ["label", "part_of", "alternate_of", "not_text"]},
                    "text": {"type": "string"},
                    "target": {"type": ["integer", "null"]},
                    "kind": {"type": "string", "enum": KINDS},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                },
                "required": ["n", "action", "text", "target", "kind", "confidence", "note"],
                "additionalProperties": False,
            },
        },
        "missed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": KINDS},
                    "box": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["text", "kind", "box"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["labels", "missed"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def paths(map_id):
    base = ROOT / "ocr" / map_id / "ai"
    return {
        "dir": base,
        "batches": base / "batches.json",
        "results": base / "results.jsonl",
        "gazetteer": ROOT / "site" / "data" / "gazetteers" / f"{map_id}.json",
    }


def load_map(map_id):
    registry = json.loads((ROOT / "site" / "data" / "maps.json").read_text())
    entry = next(m for m in registry["maps"] if m["id"] == map_id)
    gazetteer = json.loads(paths(map_id)["gazetteer"].read_text())
    image = load_image(ROOT / entry["build"]["source"], entry["build"].get("trim", False))
    return image, gazetteer


def wanted(entry):
    """Entries worth sending: unreviewed labels, and fragments rejected only
    by the automatic rules (they may be pieces of real names)."""
    if entry.get("reviewed") or entry["kind"] in SKIP_KINDS:
        return False
    if entry["status"] == "unreviewed":
        return True
    return entry["status"] == "rejected" and entry.get("note", "").startswith("Rejected automatically")


def make_tiles(gazetteer, width, height):
    def centre(e):
        x, y, w, h = e["box"]
        return x + w / 2, y + h / 2

    cells = []

    def split(x, y, size, entries):
        if len(entries) > MAX_LABELS and size > 128:
            half = size // 2
            for dx in (0, half):
                for dy in (0, half):
                    inside = [e for e in entries
                              if x + dx <= centre(e)[0] < x + dx + half and y + dy <= centre(e)[1] < y + dy + half]
                    if inside:
                        split(x + dx, y + dy, half, inside)
        else:
            cells.append((x, y, size, entries))

    grid = {}
    for e in gazetteer["entries"]:
        if wanted(e):
            cx, cy = centre(e)
            grid.setdefault((int(cx // TILE), int(cy // TILE)), []).append(e)
    for (tx, ty), entries in sorted(grid.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        split(tx * TILE, ty * TILE, TILE, entries)

    result = []
    for x, y, size, entries in cells:
        core = [x, y, min(width, x + size), min(height, y + size)]
        lo_x = min(e["box"][0] for e in entries)
        lo_y = min(e["box"][1] for e in entries)
        hi_x = max(e["box"][0] + e["box"][2] for e in entries)
        hi_y = max(e["box"][1] + e["box"][3] for e in entries)
        # Include the whole of labels that overhang the tile, within reason.
        x0 = max(min(core[0], lo_x), core[0] - MAX_GROW) - PAD
        y0 = max(min(core[1], lo_y), core[1] - MAX_GROW) - PAD
        x1 = min(max(core[2], hi_x), core[2] + MAX_GROW) + PAD
        y1 = min(max(core[3], hi_y), core[3] + MAX_GROW) + PAD
        crop = [max(0, int(x0)), max(0, int(y0)), min(width, int(x1)), min(height, int(y1))]
        scale = min(2.0, MAX_IMAGE / max(crop[2] - crop[0], crop[3] - crop[1]))
        entries.sort(key=lambda e: (e["box"][1] // 30, e["box"][0]))
        result.append({
            "id": f"x{x:05d}-y{y:05d}-s{size}",
            "crop": crop,
            "core": core,
            "scale": round(scale, 4),
            "entries": [e["id"] for e in entries],
        })
    return result


def render(image, tile, by_id):
    """The clean and the annotated crop, as JPEG bytes."""
    x0, y0, x1, y1 = tile["crop"]
    s = tile["scale"]
    size = (round((x1 - x0) * s), round((y1 - y0) * s))
    clean = image.crop((x0, y0, x1, y1)).resize(size, Image.LANCZOS)
    marked = clean.copy()
    draw = ImageDraw.Draw(marked)
    font = ImageFont.load_default(size=15)

    def to_img(px, py):
        return (px - x0) * s, (py - y0) * s

    cx0, cy0 = to_img(*tile["core"][:2])
    cx1, cy1 = to_img(*tile["core"][2:])
    for i in range(int(cx0), int(cx1), 12):
        draw.line([(i, cy0), (min(i + 6, cx1), cy0)], fill=(0, 200, 230), width=2)
        draw.line([(i, cy1), (min(i + 6, cx1), cy1)], fill=(0, 200, 230), width=2)
    for j in range(int(cy0), int(cy1), 12):
        draw.line([(cx0, j), (cx0, min(j + 6, cy1))], fill=(0, 200, 230), width=2)
        draw.line([(cx1, j), (cx1, min(j + 6, cy1))], fill=(0, 200, 230), width=2)

    for n, entry_id in enumerate(tile["entries"], 1):
        bx, by, bw, bh = by_id[entry_id]["box"]
        a = to_img(bx - 2, by - 2)
        b = to_img(bx + bw + 2, by + bh + 2)
        draw.rectangle([a, b], outline=(230, 0, 200), width=2)
        tag = str(n)
        tw, th = draw.textbbox((0, 0), tag, font=font)[2:]
        tx, ty = a[0], a[1] - th - 4
        if ty < 0:
            ty = b[1] + 2
        draw.rectangle([tx, ty, tx + tw + 4, ty + th + 3], fill=(230, 0, 200))
        draw.text((tx + 2, ty), tag, fill="white", font=font)

    def jpeg(img):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return buf.getvalue()

    return jpeg(clean), jpeg(marked)


def request_params(image, tile, by_id):
    clean, marked = render(image, tile, by_id)
    lines = []
    for n, entry_id in enumerate(tile["entries"], 1):
        e = by_id[entry_id]
        ocr = e.get("ocr") or {}
        reading = " / ".join(dict.fromkeys(filter(None, [ocr.get("text"), ocr.get("alt")]))) or "(none)"
        lines.append(f"{n}. {reading}")
    image_block = lambda data: {  # noqa: E731
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(data).decode()},
    }
    return {
        "model": MODEL,
        "max_tokens": 16000,
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        "system": SYSTEM,
        "messages": [{
            "role": "user",
            "content": [
                image_block(clean),
                image_block(marked),
                {"type": "text", "text": INSTRUCTIONS.format(scale=tile["scale"], readings="\n".join(lines))},
            ],
        }],
    }


# ---------------------------------------------------------------------------
# Applying results
# ---------------------------------------------------------------------------

def apply_results(map_id):
    p = paths(map_id)
    gazetteer = json.loads(p["gazetteer"].read_text())
    by_id = {e["id"]: e for e in gazetteer["entries"]}
    batches = json.loads(p["batches"].read_text()) if p["batches"].exists() else {"tiles": {}}
    tiles = batches["tiles"]
    if not p["results"].exists():
        sys.exit("No results downloaded yet. Run 'collect' first.")

    next_id = 1 + max(int(e["id"][1:]) for e in gazetteer["entries"])
    stats = {"label": 0, "part_of": 0, "alternate_of": 0, "not_text": 0, "missed": 0, "skipped": 0}

    for line in p["results"].read_text().splitlines():
        record = json.loads(line)
        tile = tiles.get(record["tile"]) or record.get("tile_info")
        if not tile or not record.get("output"):
            continue
        numbered = {n: by_id.get(i) for n, i in enumerate(tile["entries"], 1)}
        out = record["output"]

        for item in out["labels"]:
            e = numbered.get(item["n"])
            if not e or e.get("reviewed"):
                stats["skipped"] += 1
                continue
            action = item["action"]
            target = numbered.get(item["target"]) if item["target"] is not None else None
            e["ai"] = {k: v for k, v in {
                "action": action, "text": item["text"], "confidence": item["confidence"], "note": item["note"],
            }.items() if v}
            if e.get("note", "").startswith("Rejected automatically"):
                del e["note"]
            e.pop("into", None)

            if action == "label":
                e["name"] = item["text"]
                e["kind"] = item["kind"]
                e["status"] = "unreviewed"
            elif action == "not_text":
                e["status"] = "rejected"
            elif action in ("part_of", "alternate_of") and target and target is not e:
                e["status"] = "merged"
                e["into"] = target["id"]
                if action == "part_of":
                    x0 = min(e["box"][0], target["box"][0])
                    y0 = min(e["box"][1], target["box"][1])
                    x1 = max(e["box"][0] + e["box"][2], target["box"][0] + target["box"][2])
                    y1 = max(e["box"][1] + e["box"][3], target["box"][1] + target["box"][3])
                    target["box"] = [x0, y0, x1 - x0, y1 - y0]
                else:
                    alt = item["text"].strip("() ")
                    if alt and alt not in target["alt"]:
                        target["alt"].append(alt)
            else:
                e["status"] = "unreviewed"
                e["ai"]["note"] = (e["ai"].get("note", "") + " (unclear target)").strip()
            stats[action] += 1

        x0, y0 = tile["crop"][:2]
        s = tile["scale"]
        for m in out["missed"]:
            if len(m["box"]) != 4 or not m["text"].strip():
                continue
            bx0, by0, bx1, by1 = m["box"]
            box = [round(x0 + min(bx0, bx1) / s), round(y0 + min(by0, by1) / s),
                   round(abs(bx1 - bx0) / s), round(abs(by1 - by0) / s)]
            if box[2] < 3 or box[3] < 3 or any(overlap(box, e["box"]) > 0.3 for e in gazetteer["entries"]):
                continue
            entry = {
                "id": f"l{next_id:05d}", "name": m["text"], "alt": [], "kind": m["kind"], "box": box,
                "status": "unreviewed", "ocr": None,
                "ai": {"action": "missed", "text": m["text"], "confidence": "low",
                       "note": "Added by the AI pass; check the box covers the label"},
            }
            next_id += 1
            gazetteer["entries"].append(entry)
            by_id[entry["id"]] = entry
            stats["missed"] += 1

    write_gazetteer(p["gazetteer"], gazetteer)
    print(f"Applied to {p['gazetteer']}: {stats}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def parse_output(message):
    if message.stop_reason == "refusal":
        return None, "refused"
    if message.stop_reason == "max_tokens":
        return None, "ran out of tokens"
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, "invalid JSON"


def cost(usage, batch):
    factor = 0.5 if batch else 1.0
    return factor * (usage["input_tokens"] * PRICE_IN + usage["output_tokens"] * PRICE_OUT) / 1e6


def cmd_preview(args):
    image, gazetteer = load_map(args.map_id)
    by_id = {e["id"]: e for e in gazetteer["entries"]}
    tiles = make_tiles(gazetteer, *image.size)
    out = paths(args.map_id)["dir"] / "preview"
    out.mkdir(parents=True, exist_ok=True)
    picks = sorted(tiles, key=lambda t: -len(t["entries"]))[: args.tiles]
    for tile in picks:
        clean, marked = render(image, tile, by_id)
        (out / f"{tile['id']}-clean.jpg").write_bytes(clean)
        (out / f"{tile['id']}-marked.jpg").write_bytes(marked)
    labels = sum(len(t["entries"]) for t in tiles)
    print(f"{len(tiles)} tiles covering {labels} labels. Wrote {len(picks)} examples to {out}")


def cmd_trial(args):
    import anthropic

    client = anthropic.Anthropic()
    image, gazetteer = load_map(args.map_id)
    by_id = {e["id"]: e for e in gazetteer["entries"]}
    tiles = make_tiles(gazetteer, *image.size)
    # The busiest tiles are the hardest test.
    picks = sorted(tiles, key=lambda t: -len(t["entries"]))[: args.tiles]
    total = {"input_tokens": 0, "output_tokens": 0}
    records = []
    for tile in picks:
        params = request_params(image, tile, by_id)
        print(f"\n=== {tile['id']} ({len(tile['entries'])} labels) ===", flush=True)
        message = client.beta.messages.create(
            **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        output, error = parse_output(message)
        total["input_tokens"] += message.usage.input_tokens
        total["output_tokens"] += message.usage.output_tokens
        if error:
            print(f"  {error}")
            continue
        for item in output["labels"]:
            e = by_id[tile["entries"][item["n"] - 1]] if 0 < item["n"] <= len(tile["entries"]) else None
            ocr = (e or {}).get("ocr") or {}
            extra = f" -> #{item['target']}" if item["target"] is not None else ""
            print(f"  {item['n']:>3}. {ocr.get('text', ''):<24} => {item['action']:<12} {item['text']!r}{extra} "
                  f"[{item['kind']}, {item['confidence']}] {item['note']}")
        for m in output["missed"]:
            print(f"  missed: {m['text']!r} [{m['kind']}]")
        records.append({"tile": tile["id"], "tile_info": tile, "output": output})

    p = paths(args.map_id)
    p["dir"].mkdir(parents=True, exist_ok=True)
    (p["dir"] / "trial.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))

    labels = sum(len(t["entries"]) for t in picks)
    all_labels = sum(len(t["entries"]) for t in tiles)
    spent = cost(total, batch=False)
    estimate = cost(total, batch=True) * all_labels / max(1, labels)
    print(f"\nTrial: {len(picks)} tiles, {total['input_tokens']:,} input + {total['output_tokens']:,} output tokens, "
          f"about ${spent:.2f}.")
    print(f"Whole map: {len(tiles)} tiles, {all_labels:,} labels; estimated ${estimate:.0f} with the Batches API.")


def cmd_submit(args):
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    p = paths(args.map_id)
    if p["batches"].exists() and not args.force:
        sys.exit(f"Batches already submitted ({p['batches']}). Run 'collect', or pass --force to submit again.")
    client = anthropic.Anthropic()
    image, gazetteer = load_map(args.map_id)
    by_id = {e["id"]: e for e in gazetteer["entries"]}
    tiles = make_tiles(gazetteer, *image.size)
    if args.limit:
        tiles = tiles[: args.limit]

    batch_ids, pending, size = [], [], 0

    def send():
        batch = client.messages.batches.create(requests=pending)
        batch_ids.append(batch.id)
        print(f"Submitted batch {batch.id} with {len(pending)} tiles")

    for n, tile in enumerate(tiles, 1):
        params = request_params(image, tile, by_id)
        request_size = len(json.dumps(params))
        if pending and size + request_size > BATCH_BYTES:
            send()
            pending, size = [], 0
        pending.append(Request(custom_id=tile["id"], params=MessageCreateParamsNonStreaming(**params)))
        size += request_size
        print(f"Prepared {n}/{len(tiles)}", end="\r", flush=True)
    if pending:
        send()

    p["dir"].mkdir(parents=True, exist_ok=True)
    p["batches"].write_text(json.dumps({
        "model": MODEL,
        "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "batch_ids": batch_ids,
        "tiles": {t["id"]: t for t in tiles},
    }, indent=1))
    print(f"\nSaved {p['batches']}. Batches usually finish within an hour; then run:\n"
          f"  python3 tools/ocr/ai_correct.py collect {args.map_id}")


def cmd_collect(args):
    import anthropic

    p = paths(args.map_id)
    if not p["batches"].exists():
        sys.exit("Nothing submitted yet. Run 'submit' first.")
    batches = json.loads(p["batches"].read_text())
    client = anthropic.Anthropic()

    for batch_id in batches["batch_ids"]:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            c = batch.request_counts
            sys.exit(f"Batch {batch_id} is still {batch.processing_status}: "
                     f"{c.succeeded} done, {c.processing} processing. Try again later.")

    records, usage, failed = [], {"input_tokens": 0, "output_tokens": 0}, []
    for batch_id in batches["batch_ids"]:
        for result in client.messages.batches.results(batch_id):
            if result.result.type != "succeeded":
                failed.append(f"{result.custom_id} ({result.result.type})")
                continue
            message = result.result.message
            usage["input_tokens"] += message.usage.input_tokens
            usage["output_tokens"] += message.usage.output_tokens
            output, error = parse_output(message)
            if error:
                failed.append(f"{result.custom_id} ({error})")
                continue
            records.append({"tile": result.custom_id, "output": output})

    p["results"].write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    print(f"Downloaded {len(records)} tiles to {p['results']}. "
          f"{usage['input_tokens']:,} input + {usage['output_tokens']:,} output tokens, "
          f"about ${cost(usage, batch=True):.2f}.")
    if failed:
        print(f"{len(failed)} tiles failed and keep their OCR readings: {', '.join(failed)}")
    apply_results(args.map_id)


def cmd_apply(args):
    apply_results(args.map_id)


def main():
    parser = argparse.ArgumentParser(description="AI correction pass over a map's OCR gazetteer.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in [("preview", cmd_preview), ("trial", cmd_trial), ("submit", cmd_submit),
                     ("collect", cmd_collect), ("apply", cmd_apply)]:
        p = sub.add_parser(name)
        p.add_argument("map_id")
        p.set_defaults(fn=fn)
        if name in ("preview", "trial"):
            p.add_argument("--tiles", type=int, default=3)
        if name == "submit":
            p.add_argument("--limit", type=int, help="only the first N tiles")
            p.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
