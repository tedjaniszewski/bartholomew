from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image


# ============================================================
# SETTINGS
# ============================================================

IMAGE_ID = "101712"

DZI_FILE = (
    f"https://storage.googleapis.com/raremaps/img/dzi/"
    f"img_{IMAGE_ID}.dzi"
)

TILES_DIR = Path("./tiles")

OUTPUT = Path(
    "TopoPhysicalPalestine-101712-full.jpg"
)


# ============================================================
# READ DZI FROM LOCAL/REMOTE METADATA
# ============================================================

def read_dzi():

    import requests

    response = requests.get(
        DZI_FILE,
        timeout=60
    )

    response.raise_for_status()

    root = ET.fromstring(
        response.content
    )

    size = root.find(
        "{http://schemas.microsoft.com/deepzoom/2008}Size"
    )

    if size is None:

        size = root.find("Size")

    width = int(
        size.attrib["Width"]
    )

    height = int(
        size.attrib["Height"]
    )

    tile_size = int(
        root.attrib["TileSize"]
    )

    overlap = int(
        root.attrib.get(
            "Overlap",
            "0"
        )
    )

    image_format = root.attrib.get(
        "Format",
        "jpg"
    )

    return (
        width,
        height,
        tile_size,
        overlap,
        image_format
    )


# ============================================================
# MAXIMUM LEVEL
# ============================================================

def maximum_level(
    width,
    height
):

    dimension = max(
        width,
        height
    )

    level = 0
    size = 1

    while size < dimension:

        size *= 2
        level += 1

    return level


# ============================================================
# TILE COUNT
# ============================================================

def tile_counts(
    width,
    height,
    tile_size
):

    columns = (
        width + tile_size - 1
    ) // tile_size

    rows = (
        height + tile_size - 1
    ) // tile_size

    return columns, rows


# ============================================================
# MAIN
# ============================================================

def main():

    (
        width,
        height,
        tile_size,
        overlap,
        image_format
    ) = read_dzi()

    level = maximum_level(
        width,
        height
    )

    columns, rows = tile_counts(
        width,
        height,
        tile_size
    )

    print()
    print("=" * 70)
    print("RAREMAPS DEEP-ZOOM TILE STITCHER")
    print("=" * 70)
    print()

    print(
        f"Image dimensions: "
        f"{width:,} × {height:,}"
    )

    print(
        f"Maximum level:    "
        f"{level}"
    )

    print(
        f"Tile size:        "
        f"{tile_size}"
    )

    print(
        f"Overlap:          "
        f"{overlap}"
    )

    print(
        f"Tile grid:        "
        f"{columns} × {rows}"
    )

    print()

    # --------------------------------------------------------
    # Check every tile before creating the canvas.
    # --------------------------------------------------------

    missing = []

    for y in range(rows):

        for x in range(columns):

            filename = (
                TILES_DIR
                / f"{level}-{x}-{y}.{image_format}"
            )

            if not filename.exists():

                missing.append(
                    (x, y)
                )

    if missing:

        print(
            f"ERROR: {len(missing)} "
            f"tiles are missing."
        )

        print()

        for x, y in missing:

            print(
                f"  {level}-{x}-{y}."
                f"{image_format}"
            )

        print()
        print(
            "Download all tiles before stitching."
        )

        return

    # --------------------------------------------------------
    # Create exact-size master image.
    # --------------------------------------------------------

    canvas = Image.new(
        "RGB",
        (
            width,
            height
        )
    )

    print(
        f"Created {width:,} × "
        f"{height:,} canvas."
    )

    print()

    # --------------------------------------------------------
    # Stitch.
    # --------------------------------------------------------

    total = columns * rows
    count = 0

    for y in range(rows):

        for x in range(columns):

            count += 1

            filename = (
                TILES_DIR
                / f"{level}-{x}-{y}."
                f"{image_format}"
            )

            print(
                f"[{count}/{total}] "
                f"{filename.name}"
            )

            tile = Image.open(
                filename
            )

            tile.load()

            # ------------------------------------------------
            # DZI overlap handling.
            #
            # Every non-edge tile contains one pixel of
            # overlap on its left/top/right/bottom edges.
            # Remove those overlaps before placing it.
            # ------------------------------------------------

            left = (
                overlap
                if x > 0
                else 0
            )

            top = (
                overlap
                if y > 0
                else 0
            )

            right = (
                overlap
                if x < columns - 1
                else 0
            )

            bottom = (
                overlap
                if y < rows - 1
                else 0
            )

            crop_right = (
                tile.width - right
            )

            crop_bottom = (
                tile.height - bottom
            )

            tile = tile.crop(
                (
                    left,
                    top,
                    crop_right,
                    crop_bottom
                )
            )

            # ------------------------------------------------
            # Position in original image coordinates.
            # ------------------------------------------------

            destination_x = (
                x * tile_size
            )

            destination_y = (
                y * tile_size
            )

            canvas.paste(
                tile,
                (
                    destination_x,
                    destination_y
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
        f"{canvas.width:,} × "
        f"{canvas.height:,}"
    )

    print(
        f"Output: "
        f"{OUTPUT.resolve()}"
    )

    print()


if __name__ == "__main__":
    main()