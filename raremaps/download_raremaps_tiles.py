import time
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from PIL import Image


# ============================================================
# SETTINGS
# ============================================================

IMAGE_ID = "101712"

DZI_URL = (
    f"https://storage.googleapis.com/raremaps/img/dzi/"
    f"img_{IMAGE_ID}.dzi"
)

TILES_BASE_URL = (
    f"https://storage.googleapis.com/raremaps/img/dzi/"
    f"img_{IMAGE_ID}_files"
)

TILES_DIR = Path("./tiles")

# Pause between requests.
DELAY = 0.25

# Number of retries for temporary failures.
RETRIES = 2


# ============================================================
# HTTP SESSION
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) "
        "Gecko/20100101 Firefox/156.0"
    ),
    "Referer": (
        "https://raremaps.com/gallery/detail/101712/"
        "topographical-physical-map-of-palestine-compiled-from-"
        "palestine-exploration-fund-surveys-and-other-authorities-"
        "under-the-direction-of-jg-bartholomew-frse-frgs-and-"
        "edited-by-george-adam-smith-dd-lld"
    ),
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# READ DZI
# ============================================================

def read_dzi():

    print()
    print("Downloading DZI metadata:")
    print(DZI_URL)
    print()

    response = session.get(
        DZI_URL,
        timeout=60
    )

    response.raise_for_status()

    print("DZI metadata:")
    print(response.text)
    print()

    root = ET.fromstring(response.content)

    # DZI uses an XML namespace.
    size = root.find(
        "{http://schemas.microsoft.com/deepzoom/2008}Size"
    )

    if size is None:

        # Some DZI files omit the namespace.
        size = root.find("Size")

    if size is None:

        raise RuntimeError(
            "Could not find <Size> in DZI file."
        )

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
# CALCULATE MAXIMUM DZI LEVEL
# ============================================================

def maximum_level(width, height):

    """
    DZI level 0 is the smallest representation.

    Each successive level doubles the resolution.

    The maximum level is:

        ceil(log2(max(width, height)))
    """

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
# NUMBER OF TILES AT MAXIMUM LEVEL
# ============================================================

def tile_counts(
    width,
    height,
    tile_size
):

    columns = (
        (width + tile_size - 1)
        // tile_size
    )

    rows = (
        (height + tile_size - 1)
        // tile_size
    )

    return columns, rows


# ============================================================
# TILE URL
# ============================================================

def tile_url(
    level,
    x,
    y,
    image_format
):

    return (
        f"{TILES_BASE_URL}/"
        f"{level}/"
        f"{x}_{y}."
        f"{image_format}"
    )


# ============================================================
# LOCAL TILE NAME
# ============================================================

def tile_filename(
    level,
    x,
    y,
    image_format
):

    return (
        TILES_DIR
        / f"{level}-{x}-{y}.{image_format}"
    )


# ============================================================
# DOWNLOAD ONE TILE
# ============================================================

def download_tile(
    level,
    x,
    y,
    image_format
):

    filename = tile_filename(
        level,
        x,
        y,
        image_format
    )

    url = tile_url(
        level,
        x,
        y,
        image_format
    )

    # --------------------------------------------------------
    # Already downloaded?
    # --------------------------------------------------------

    if (
        filename.exists()
        and filename.stat().st_size > 0
    ):

        print(
            f"SKIP  {filename.name}"
        )

        return True

    # --------------------------------------------------------
    # Download
    # --------------------------------------------------------

    for attempt in range(
        RETRIES + 1
    ):

        try:

            response = session.get(
                url,
                timeout=60
            )

            content_type = (
                response.headers
                .get(
                    "Content-Type",
                    ""
                )
                .lower()
            )

            if (
                response.status_code == 200
                and "image" in content_type
            ):

                # Verify that it really is an image.
                image = Image.open(
                    BytesIO(
                        response.content
                    )
                )

                image.load()

                # Save the ORIGINAL bytes.
                with open(
                    filename,
                    "wb"
                ) as f:

                    f.write(
                        response.content
                    )

                print(
                    f"OK    {filename.name}"
                )

                return True

            print(
                f"MISS  {filename.name} "
                f"(HTTP {response.status_code})"
            )

            if response.status_code in (
                403,
                429,
                500,
                502,
                503,
                504,
            ):

                if attempt < RETRIES:

                    wait = (
                        2 * (attempt + 1)
                    )

                    print(
                        f"      "
                        f"retrying in "
                        f"{wait} seconds..."
                    )

                    time.sleep(
                        wait
                    )

                    continue

            return False

        except Exception as e:

            print(
                f"MISS  {filename.name} "
                f"({type(e).__name__}: {e})"
            )

            if attempt < RETRIES:

                time.sleep(
                    2 * (attempt + 1)
                )

                continue

            return False


# ============================================================
# MAIN
# ============================================================

def main():

    TILES_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

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

    total = (
        columns * rows
    )

    print()
    print("=" * 70)
    print("RAREMAPS DEEP-ZOOM TILE DOWNLOADER")
    print("=" * 70)
    print()

    print(
        f"Image ID:       {IMAGE_ID}"
    )

    print(
        f"Full resolution: "
        f"{width:,} × {height:,}"
    )

    print(
        f"Tile size:       "
        f"{tile_size}"
    )

    print(
        f"Overlap:         "
        f"{overlap}"
    )

    print(
        f"Format:          "
        f"{image_format}"
    )

    print(
        f"Maximum level:   "
        f"{level}"
    )

    print(
        f"Tile columns:    "
        f"{columns}"
    )

    print(
        f"Tile rows:       "
        f"{rows}"
    )

    print(
        f"Total tiles:     "
        f"{total:,}"
    )

    print(
        f"Tile directory:  "
        f"{TILES_DIR.resolve()}"
    )

    print()
    print("=" * 70)
    print()

    downloaded_now = 0
    already_present = 0
    missing = []

    # --------------------------------------------------------
    # Download all full-resolution tiles.
    # --------------------------------------------------------

    for y in range(rows):

        print(
            f"========== ROW {y + 1}/{rows} =========="
        )

        for x in range(columns):

            filename = tile_filename(
                level,
                x,
                y,
                image_format
            )

            if (
                filename.exists()
                and filename.stat().st_size > 0
            ):

                already_present += 1

                print(
                    f"SKIP  {filename.name}"
                )

                continue

            success = download_tile(
                level,
                x,
                y,
                image_format
            )

            if success:

                downloaded_now += 1

            else:

                missing.append(
                    (x, y)
                )

            time.sleep(
                DELAY
            )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    print()
    print()
    print("=" * 70)
    print("DOWNLOAD FINISHED")
    print("=" * 70)
    print()

    print(
        f"Already present: "
        f"{already_present}"
    )

    print(
        f"Downloaded now:  "
        f"{downloaded_now}"
    )

    print(
        f"Total available: "
        f"{already_present + downloaded_now}"
    )

    print(
        f"Still missing:   "
        f"{len(missing)}"
    )

    print()

    if missing:

        print(
            "TILES NOT YET DOWNLOADED:"
        )

        print()

        for x, y in missing:

            print(
                f"  {level}-{x}-{y}."
                f"{image_format}"
            )

        print()
        print(
            "Run the program again to retry them."
        )

    else:

        print(
            f"ALL {total:,} FULL-RESOLUTION "
            f"TILES ARE PRESENT."
        )

    print()


if __name__ == "__main__":
    main()