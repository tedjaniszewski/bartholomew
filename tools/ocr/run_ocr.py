"""
Read all the text on a map with OCR.

The sheet is read in overlapping windows by two engines:

- OnnxTR (docTR models: DB-ResNet50 detection, PARSeq recognition), which
  finds individual words, rotated ones included, and reads Latin text well.
  Its words are the basis of the gazetteer.
- RapidOCR (PaddleOCR models), which finds whole labels and gives a second,
  independent reading that the review tool offers as a suggestion.

Output is ocr/<map-id>/words.json, in the pixel space of the map's tiles.
Turn it into a reviewable gazetteer with build_gazetteer.py.

    python3 tools/ocr/run_ocr.py palestine-1901

Requires: pip install "onnxtr[cpu]" rapidocr-onnxruntime pillow
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from make_iiif_tiles import load_image  # noqa: E402

WINDOW = 1024
OVERLAP = 256


def windows(width, height):
    step = WINDOW - OVERLAP
    xs = list(range(0, max(width - WINDOW, 0) + 1, step))
    ys = list(range(0, max(height - WINDOW, 0) + 1, step))
    if xs[-1] + WINDOW < width:
        xs.append(width - WINDOW)
    if ys[-1] + WINDOW < height:
        ys.append(height - WINDOW)
    return [(x, y) for y in ys for x in xs]


def edge_distance(poly, x0, y0, w, h):
    """How far a detection sits from the edge of the window it came from.
    Where windows overlap, the copy furthest from an edge is the one read
    with the most context, and is kept."""
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(min(xs) - x0, x0 + w - max(xs), min(ys) - y0, y0 + h - max(ys))


def bbox(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def iou(a, b):
    ax0, ay0, ax1, ay1 = bbox(a)
    bx0, by0, bx1, by1 = bbox(b)
    iw, ih = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / ((ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter)


def dedupe(items):
    """Drop detections duplicated across overlapping windows."""
    items.sort(key=lambda d: -d["_edge"])
    kept = []
    # Bucket by coarse position so this stays fast with tens of thousands of words.
    grid = {}
    for d in items:
        x0, y0, x1, y1 = bbox(d["poly"])
        cells = {(int(cx) // 256, int(cy) // 256) for cx in (x0, x1) for cy in (y0, y1)}
        neighbours = {id(k): k for c in cells for k in grid.get(c, [])}.values()
        if any(iou(d["poly"], k["poly"]) > 0.3 for k in neighbours):
            continue
        kept.append(d)
        for c in cells:
            grid.setdefault(c, []).append(d)
    for d in kept:
        del d["_edge"]
    return kept


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("map_id")
    parser.add_argument("--limit", type=int, help="only read the first N windows (for testing)")
    args = parser.parse_args()

    from onnxtr.models import ocr_predictor
    from rapidocr_onnxruntime import RapidOCR

    registry = json.loads((ROOT / "site" / "data" / "maps.json").read_text())
    entry = next(m for m in registry["maps"] if m["id"] == args.map_id)
    build = entry["build"]
    image = load_image(ROOT / build["source"], build.get("trim", False))
    W, H = image.size

    words_model = ocr_predictor(
        det_arch="db_resnet50",
        reco_arch="parseq",
        assume_straight_pages=False,
        detect_orientation=False,
        straighten_pages=False,
    )
    lines_model = RapidOCR()

    jobs = windows(W, H)[: args.limit]
    words, lines = [], []
    started = time.time()
    for n, (x0, y0) in enumerate(jobs, 1):
        crop = np.asarray(image.crop((x0, y0, x0 + WINDOW, y0 + WINDOW)))
        cw, ch = crop.shape[1], crop.shape[0]

        page = words_model([crop]).pages[0]
        for block in page.blocks:
            for line in block.lines:
                for word in line.words:
                    poly = [[x0 + px * cw, y0 + py * ch] for px, py in word.geometry]
                    words.append({
                        "text": word.value,
                        "conf": round(float(word.confidence), 3),
                        "poly": [[round(x, 1), round(y, 1)] for x, y in poly],
                        "_edge": edge_distance(poly, x0, y0, cw, ch),
                    })

        found, _ = lines_model(crop)
        for box, text, conf in found or []:
            poly = [[x0 + float(px), y0 + float(py)] for px, py in box]
            lines.append({
                "text": text,
                "conf": round(float(conf), 3),
                "poly": [[round(x, 1), round(y, 1)] for x, y in poly],
                "_edge": edge_distance(poly, x0, y0, cw, ch),
            })

        elapsed = time.time() - started
        print(f"window {n}/{len(jobs)} at {x0},{y0}: {len(words)} words so far, "
              f"{elapsed / n * (len(jobs) - n) / 60:.0f} min left", flush=True)

    out = ROOT / "ocr" / args.map_id / "words.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "map": args.map_id,
        "source": build["source"],
        "image": {"width": W, "height": H},
        "engines": {
            "words": "OnnxTR db_resnet50 + parseq",
            "lines": "RapidOCR (PaddleOCR) det + rec",
        },
        "words": dedupe(words),
        "lines": dedupe(lines),
    }
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    print(f"Wrote {len(result['words'])} words and {len(result['lines'])} lines to {out}")


if __name__ == "__main__":
    main()
