"""
Generate a static IIIF Image API 3.0 (level 0) tile pyramid from one image.

The output is a directory of plain JPEG files plus an info.json, which any
static web host (GitHub Pages, S3, Netlify, ...) can serve with no image
server. Tile paths follow the IIIF URL pattern

    {id}/{region}/{size}/{rotation}/{quality}.{format}

and are computed with exactly the same arithmetic OpenSeadragon's
IIIFTileSource uses, so every tile the viewer asks for exists on disk.

Usage:

    python3 tools/make_iiif_tiles.py SOURCE_IMAGE OUTPUT_DIR [--tile 512] [--quality 88] [--trim]

Example:

    python3 tools/make_iiif_tiles.py \
        geographicus/TopoPhysicalPalestine-bartholomew-1901-full.jpg \
        site/iiif/bartholomew-1901-geographicus

The "id" written into info.json is a placeholder; the site rewrites it at
load time to the info.json's own URL, so the tiles work from any host path.
"""

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def load_image(path, trim=False):
    """Open a source image as RGB, optionally cropping black padding.

    The OCR tools load images through this too, so their pixel coordinates
    match the tiles exactly.
    """
    image = Image.open(path).convert("RGB")
    if trim:
        bbox = image.convert("L").point(lambda v: 255 if v > 24 else 0).getbbox()
        if bbox and bbox != (0, 0) + image.size:
            print(f"Trimming padding: keeping {bbox}")
            image = image.crop(bbox)
    return image


def scale_factors_for(width, height, tile):
    """Powers of two, down to the level where the whole image fits one tile."""
    factors = [1]
    while math.ceil(width / factors[-1]) > tile or math.ceil(height / factors[-1]) > tile:
        factors.append(factors[-1] * 2)
    return factors


def size_param(w, h, full_w, full_h):
    return "max" if (w, h) == (full_w, full_h) else f"{w},{h}"


def save(img, path, quality):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=quality, optimize=True, progressive=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tile", type=int, default=512)
    parser.add_argument("--quality", type=int, default=88)
    parser.add_argument("--trim", action="store_true",
                        help="crop away black padding around the scan (e.g. left by stitching tiles)")
    args = parser.parse_args()

    tile = args.tile
    out = args.output

    print(f"Reading {args.source} ...")
    full = load_image(args.source, args.trim)
    W, H = full.size
    factors = scale_factors_for(W, H, tile)
    print(f"Image {W} x {H}; scale factors {factors}")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    count = 0
    level_img = full
    for sf in factors:
        lw, lh = math.ceil(W / sf), math.ceil(H / sf)
        if sf != 1:
            # Downsample from the previous level: faster, and the 2x box
            # steps keep fine linework crisp.
            level_img = level_img.resize((lw, lh), Image.LANCZOS)

        if lw < tile and lh < tile:
            # Smallest level: OpenSeadragon asks for the whole image.
            save(level_img, out / "full" / size_param(lw, lh, W, H) / "0" / "default.jpg", args.quality)
            count += 1
            continue

        cols, rows = math.ceil(lw / tile), math.ceil(lh / tile)
        region_size = tile * sf
        for y in range(rows):
            for x in range(cols):
                rx, ry = x * region_size, y * region_size
                rw, rh = min(region_size, W - rx), min(region_size, H - ry)
                sw, sh = min(tile, lw - x * tile), min(tile, lh - y * tile)
                region = "full" if (rx, ry, rw, rh) == (0, 0, W, H) else f"{rx},{ry},{rw},{rh}"
                crop = level_img.crop((x * tile, y * tile, x * tile + sw, y * tile + sh))
                save(crop, out / region / size_param(sw, sh, W, H) / "0" / "default.jpg", args.quality)
                count += 1
        print(f"  scale factor {sf:>3}: {lw} x {lh}, {cols * rows} tiles")

    info = {
        "@context": "http://iiif.io/api/image/3/context.json",
        "id": "PLACEHOLDER",
        "type": "ImageService3",
        "protocol": "http://iiif.io/api/image",
        "profile": "level0",
        "width": W,
        "height": H,
        "sizes": [
            {"width": math.ceil(W / sf), "height": math.ceil(H / sf)}
            for sf in reversed(factors)
        ],
        "tiles": [{"width": tile, "height": tile, "scaleFactors": factors}],
        "extraFormats": [],
        "extraQualities": [],
    }
    (out / "info.json").write_text(json.dumps(info, indent=2))

    # A small preview, handy for the map switcher and social cards.
    thumb = full.copy()
    thumb.thumbnail((600, 600), Image.LANCZOS)
    save(thumb, out / "thumbnail.jpg", 85)

    print(f"Wrote {count} tiles and info.json to {out}")


if __name__ == "__main__":
    sys.exit(main())
