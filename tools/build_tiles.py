"""
Build the IIIF tile pyramid for every map in site/data/maps.json that has a
"build.source" image. Tiles land next to the map's "image" (info.json) path.

    python3 tools/build_tiles.py           # build what is missing or stale
    python3 tools/build_tiles.py --force   # rebuild everything
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    registry = json.loads((SITE / "data" / "maps.json").read_text())
    for entry in registry["maps"]:
        build = entry.get("build") or {}
        if not build.get("source"):
            continue
        source = ROOT / build["source"]
        out = (SITE / entry["image"]).parent
        info = out / "info.json"
        if not args.force and info.exists() and info.stat().st_mtime >= source.stat().st_mtime:
            print(f"{entry['id']}: up to date")
            continue
        print(f"{entry['id']}: building tiles")
        command = [sys.executable, str(ROOT / "tools" / "make_iiif_tiles.py"), str(source), str(out)]
        if build.get("trim"):
            command.append("--trim")
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
