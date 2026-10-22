"""
Back up a very large scan to GitHub, which refuses files over 100 MB.

Two ways:

1. chunk / unchunk (recommended for backup): cut the file's bytes into
   pieces under GitHub's limit. Nothing is decoded or re-encoded, the pieces
   together are exactly the size of the original, and unchunk restores a
   byte-for-byte identical file (checked against its SHA-256).

    python3 tools/split_scan.py chunk   berkeley/hv064kb8193_00_0001.jp2 berkeley/hv064kb8193_00_0001
    python3 tools/split_scan.py unchunk berkeley/hv064kb8193_00_0001 berkeley/hv064kb8193_00_0001.jp2

2. split / join: cut the image into a grid of image tiles, saved losslessly,
   which can be opened and worked on individually. Lossless tiles are much
   larger than a lossy original (Berkeley's JPEG 2000 grew about six times
   in a test), so use this for working copies rather than backup.

    python3 tools/split_scan.py split berkeley/hv064kb8193_00_0001.jp2 berkeley/tiles
    python3 tools/split_scan.py join  berkeley/tiles berkeley/rejoined.png

Options for chunk:
    --size 90         piece size in MB (default 90)

Options for split:
    --tile 4096       tile size in pixels (default 4096)
    --format jp2      lossless JPEG 2000 (default, compact) or png

The joined image has exactly the original pixels, but it is a new file
(e.g. PNG), not a byte-for-byte copy of the original JPEG 2000. Keep the
original archived locally as well.

Large scans need memory: about 3 bytes per pixel, so a 600-megapixel scan
needs roughly 2 GB free.

Requires: pip install pillow
"""

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixels_sha256(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def chunk(args):
    source = Path(args.source)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    piece = args.size * 1_000_000
    parts = []
    with open(source, "rb") as f:
        n = 0
        while data := f.read(piece):
            name = f"{source.name}.part{n:03d}"
            (out / name).write_bytes(data)
            parts.append({"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            print(f"  {name}  {len(data) / 1e6:.0f} MB")
            n += 1
    manifest = {
        "source": source.name,
        "source_bytes": source.stat().st_size,
        "source_sha256": file_sha256(source),
        "parts": parts,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"Wrote {len(parts)} pieces and manifest.json to {out}. Rebuild with:\n"
          f"  python3 tools/split_scan.py unchunk {out} {source.name}")


def unchunk(args):
    folder = Path(args.parts)
    manifest = json.loads((folder / "manifest.json").read_text())
    target = Path(args.output)
    digest = hashlib.sha256()
    with open(target, "wb") as f:
        for part in manifest["parts"]:
            data = (folder / part["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != part["sha256"]:
                sys.exit(f"{part['file']} is damaged (checksum mismatch).")
            f.write(data)
            digest.update(data)
    if digest.hexdigest() != manifest["source_sha256"]:
        sys.exit("The rebuilt file does not match the original's checksum.")
    print(f"Rebuilt {target} ({manifest['source_bytes'] / 1e6:.0f} MB), identical to the original.")


def split(args):
    source = Path(args.source)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Reading {source} ({source.stat().st_size / 1e6:.0f} MB); large scans take a few minutes...")
    started = time.time()
    image = Image.open(source)
    info = {k: v for k, v in image.info.items() if isinstance(v, (int, float, str, tuple))}
    image.load()
    width, height = image.size
    print(f"{width} x {height} {image.mode}, decoded in {time.time() - started:.0f} s")

    size = args.tile
    cols, rows = math.ceil(width / size), math.ceil(height / size)
    ext = "jp2" if args.format == "jp2" else "png"
    tiles = []
    for row in range(rows):
        for col in range(cols):
            box = (col * size, row * size, min(width, (col + 1) * size), min(height, (row + 1) * size))
            tile = image.crop(box)
            name = f"r{row:02d}-c{col:02d}.{ext}"
            path = out / name
            if ext == "jp2":
                # No quality layers and the reversible wavelet: lossless.
                tile.save(path, "JPEG2000", irreversible=False)
            else:
                tile.save(path, "PNG", optimize=True)

            expected = pixels_sha256(tile)
            with Image.open(path) as check:
                if pixels_sha256(check.convert(tile.mode)) != expected:
                    sys.exit(f"Verification failed for {path}: the saved tile differs from the original.")
            megabytes = path.stat().st_size / 1e6
            if megabytes > 95:
                print(f"  warning: {name} is {megabytes:.0f} MB; use a smaller --tile to stay under GitHub's 100 MB limit")
            tiles.append({"file": name, "box": list(box), "pixels_sha256": expected})
            print(f"  {name}  {box[2] - box[0]} x {box[3] - box[1]}  {megabytes:.1f} MB  verified")

    manifest = {
        "source": source.name,
        "source_bytes": source.stat().st_size,
        "source_sha256": file_sha256(source),
        "width": width,
        "height": height,
        "mode": image.mode,
        "info": info,
        "tile_size": size,
        "format": ext,
        "tiles": tiles,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=list))
    total = sum((out / t["file"]).stat().st_size for t in tiles) / 1e6
    print(f"Wrote {len(tiles)} tiles ({total:.0f} MB in all) and manifest.json to {out}")


def join(args):
    folder = Path(args.tiles)
    manifest = json.loads((folder / "manifest.json").read_text())
    image = Image.new(manifest["mode"], (manifest["width"], manifest["height"]))
    for t in manifest["tiles"]:
        with Image.open(folder / t["file"]) as tile:
            tile = tile.convert(manifest["mode"])
            if pixels_sha256(tile) != t["pixels_sha256"]:
                sys.exit(f"{t['file']} does not match its checksum; the tile is damaged.")
            image.paste(tile, tuple(t["box"][:2]))
        print(f"  {t['file']} ok")
    dpi = manifest.get("info", {}).get("dpi")
    print(f"Writing {args.output} ...")
    image.save(args.output, **({"dpi": tuple(dpi)} if dpi else {}))
    print(f"Rebuilt {manifest['width']} x {manifest['height']} image from {len(manifest['tiles'])} tiles.")


def main():
    parser = argparse.ArgumentParser(description="Back up a large scan in pieces GitHub accepts.")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("chunk")
    c.add_argument("source")
    c.add_argument("output")
    c.add_argument("--size", type=int, default=90)
    c.set_defaults(fn=chunk)
    u = sub.add_parser("unchunk")
    u.add_argument("parts")
    u.add_argument("output")
    u.set_defaults(fn=unchunk)
    s = sub.add_parser("split")
    s.add_argument("source")
    s.add_argument("output")
    s.add_argument("--tile", type=int, default=4096)
    s.add_argument("--format", choices=["jp2", "png"], default="jp2")
    s.set_defaults(fn=split)
    j = sub.add_parser("join")
    j.add_argument("tiles")
    j.add_argument("output")
    j.set_defaults(fn=join)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
