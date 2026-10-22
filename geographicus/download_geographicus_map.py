import time
from pathlib import Path
from io import BytesIO

import requests
from PIL import Image


# ============================================================
# SETTINGS
# ============================================================

BASE_URL = (
    "https://www.geographicus.com/mm5/graphics/00000001/"
    "zoomify/TopoPhysicalPalestine-bartholomew-1901"
)

LEVEL = 6

# We have established these from the Zoomify tile structure.
COLUMNS = 27
ROWS = 40
OFFSET = 377

TILE_SIZE = 256

# Tiles will be stored here.
TILES_DIR = Path("./tiles")

# Pause between requests.
# 0.5 seconds is deliberately conservative.
DELAY = 0.5

# Number of times to retry a temporarily failed request.
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
        "https://www.geographicus.com/P/AntiqueMap/"
        "topophysicalpalestine-bartholomew-1901"
    ),
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# FILE NAME
# ============================================================

def tile_filename(x, y):
    """
    Local filename for a tile.

    Example:

        6-24-0.jpg

    """

    return TILES_DIR / f"{LEVEL}-{x}-{y}.jpg"


# ============================================================
# TILE GROUP
# ============================================================

def tile_group(x, y):
    """
    Calculate the Zoomify TileGroup containing this tile.
    """

    global_number = (
        OFFSET
        + y * COLUMNS
        + x
    )

    return global_number // 256


# ============================================================
# TILE URL
# ============================================================

def tile_url(x, y):
    """
    Construct the URL for one tile.
    """

    group = tile_group(x, y)

    return (
        f"{BASE_URL}/TileGroup{group}/"
        f"{LEVEL}-{x}-{y}.jpg"
    )


# ============================================================
# DOWNLOAD ONE TILE
# ============================================================

def download_tile(x, y):
    """
    Download one tile.

    Returns True if successful.
    Returns False if the tile could not be downloaded.
    """

    filename = tile_filename(x, y)
    url = tile_url(x, y)

    # --------------------------------------------------------
    # Already downloaded?
    # --------------------------------------------------------

    if filename.exists() and filename.stat().st_size > 0:

        print(
            f"SKIP  {LEVEL}-{x}-{y} "
            f"(already downloaded)"
        )

        return True

    # --------------------------------------------------------
    # Try the request
    # --------------------------------------------------------

    for attempt in range(RETRIES + 1):

        try:

            response = session.get(
                url,
                timeout=60
            )

            content_type = response.headers.get(
                "Content-Type",
                ""
            ).lower()

            # ------------------------------------------------
            # Successful image
            # ------------------------------------------------

            if (
                response.status_code == 200
                and "image" in content_type
            ):

                # Make sure it really is a readable image.
                image = Image.open(
                    BytesIO(response.content)
                )

                image.load()

                # Save the ORIGINAL JPEG bytes rather than
                # recompressing the tile.
                with open(filename, "wb") as f:
                    f.write(response.content)

                print(
                    f"OK    {LEVEL}-{x}-{y} "
                    f"-> {filename}"
                )

                return True

            # ------------------------------------------------
            # Failed request
            # ------------------------------------------------

            print(
                f"MISS  {LEVEL}-{x}-{y} "
                f"(HTTP {response.status_code})"
            )

            # If this looks temporary, retry.
            if response.status_code in (
                403,
                429,
                500,
                502,
                503,
                504,
            ):

                if attempt < RETRIES:

                    print(
                        f"      retrying in "
                        f"{2 * (attempt + 1)} seconds..."
                    )

                    time.sleep(
                        2 * (attempt + 1)
                    )

                    continue

            return False

        except Exception as e:

            print(
                f"MISS  {LEVEL}-{x}-{y} "
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

    print()
    print("=" * 70)
    print("GEOGRAPHICUS LEVEL-6 TILE DOWNLOADER")
    print("=" * 70)
    print()

    print(f"Level:       {LEVEL}")
    print(f"Columns:     {COLUMNS}")
    print(f"Rows:        {ROWS}")
    print(f"Total tiles: {COLUMNS * ROWS:,}")
    print(f"Tile size:   {TILE_SIZE} × {TILE_SIZE}")
    print(f"Offset:      {OFFSET}")
    print(f"Tile folder: {TILES_DIR.resolve()}")
    print()

    downloaded_before = 0
    downloaded_now = 0
    missing = []

    # --------------------------------------------------------
    # Download every tile.
    #
    # We go row-by-row, left-to-right, which is also the
    # natural Zoomify ordering.
    # --------------------------------------------------------

    for y in range(ROWS):

        print()
        print(
            f"========== ROW {y + 1}/{ROWS} =========="
        )

        for x in range(COLUMNS):

            filename = tile_filename(x, y)

            if (
                filename.exists()
                and filename.stat().st_size > 0
            ):

                downloaded_before += 1

                print(
                    f"SKIP  {LEVEL}-{x}-{y}"
                )

                continue

            success = download_tile(x, y)

            if success:
                downloaded_now += 1
            else:
                missing.append(
                    (x, y)
                )

            time.sleep(DELAY)

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    print()
    print()
    print("=" * 70)
    print("DOWNLOAD FINISHED")
    print("=" * 70)
    print()

    print(
        f"Already present:  {downloaded_before}"
    )

    print(
        f"Downloaded now:   {downloaded_now}"
    )

    print(
        f"Total available:  "
        f"{downloaded_before + downloaded_now}"
    )

    print(
        f"Still missing:    {len(missing)}"
    )

    print()

    if missing:

        print("=" * 70)
        print("TILES NOT YET DOWNLOADED")
        print("=" * 70)
        print()

        for x, y in missing:

            group = tile_group(x, y)

            print(
                f"{LEVEL}-{x}-{y}.jpg "
                f"(TileGroup{group})"
            )

        print()
        print(
            "Run the program again to retry these tiles."
        )

    else:

        print(
            "ALL 1,080 TILES HAVE BEEN DOWNLOADED."
        )

    print()


if __name__ == "__main__":
    main()