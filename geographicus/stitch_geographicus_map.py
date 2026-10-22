from pathlib import Path

from PIL import Image


# ============================================================
# SETTINGS
# ============================================================

LEVEL = 6

COLUMNS = 27
ROWS = 40

TILE_SIZE = 256

TILES_DIR = Path("./tiles")

OUTPUT = Path(
    "TopoPhysicalPalestine-bartholomew-1901-full.jpg"
)


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("GEOGRAPHICUS TILE STITCHER")
    print("=" * 70)
    print()

    # --------------------------------------------------------
    # Check that every tile exists.
    # --------------------------------------------------------

    missing = []

    for y in range(ROWS):

        for x in range(COLUMNS):

            filename = (
                TILES_DIR
                / f"{LEVEL}-{x}-{y}.jpg"
            )

            if not filename.exists():

                missing.append(
                    (x, y)
                )

    if missing:

        print(
            f"ERROR: {len(missing)} tiles are missing."
        )

        print()
        print("Missing tiles:")

        for x, y in missing:

            print(
                f"  {LEVEL}-{x}-{y}.jpg"
            )

        print()
        print(
            "Download all tiles before stitching."
        )

        return

    # --------------------------------------------------------
    # Create master canvas.
    # --------------------------------------------------------

    width = COLUMNS * TILE_SIZE
    height = ROWS * TILE_SIZE

    print(
        f"Creating canvas:"
    )

    print(
        f"  {width:,} × {height:,} pixels"
    )

    print()

    canvas = Image.new(
        "RGB",
        (
            width,
            height
        )
    )

    # --------------------------------------------------------
    # Paste tiles.
    # --------------------------------------------------------

    total = COLUMNS * ROWS
    count = 0

    for y in range(ROWS):

        for x in range(COLUMNS):

            count += 1

            filename = (
                TILES_DIR
                / f"{LEVEL}-{x}-{y}.jpg"
            )

            print(
                f"[{count}/{total}] "
                f"{filename.name}"
            )

            tile = Image.open(
                filename
            )

            tile.load()

            canvas.paste(
                tile,
                (
                    x * TILE_SIZE,
                    y * TILE_SIZE
                )
            )

            tile.close()

    # --------------------------------------------------------
    # Save.
    # --------------------------------------------------------

    print()
    print("Saving master image...")
    print()

    canvas.save(
        OUTPUT,
        "JPEG",
        quality=95,
        subsampling=0
    )

    print()
    print("=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print()

    print(
        f"Dimensions: "
        f"{canvas.width:,} × {canvas.height:,}"
    )

    print(
        f"Output: "
        f"{OUTPUT.resolve()}"
    )

    print()


if __name__ == "__main__":
    main()