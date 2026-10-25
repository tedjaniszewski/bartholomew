"""
Fetch the portraits on the About page from the subjects' Wikipedia articles.

    python3 tools/fetch_portraits.py

For each person this takes the lead image of their English Wikipedia
article, saves a 480px-wide copy to site/img/portraits/<key>.jpg, and writes
the image's author and licence (from Wikimedia Commons) into the caption on
site/about.html, linking to the file's page. Rerun at any time; it replaces
the images and captions.

Requires: pip install pillow
"""

import html
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "img" / "portraits"
ABOUT = ROOT / "site" / "about.html"
WIDTH = 480
USER_AGENT = "Bartholomew map site (portrait fetcher; https://github.com/tedjaniszewski/bartholomew)"

PEOPLE = {
    "bartholomew": "John_George_Bartholomew",
    "smith": "George_Adam_Smith",
}


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def get_json(url):
    return json.loads(get(url))


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def lead_image(article):
    """The file name of the article's lead image."""
    api = ("https://en.wikipedia.org/w/api.php?action=query&format=json&prop=pageimages"
           "&piprop=name&titles=" + urllib.parse.quote(article))
    pages = get_json(api)["query"]["pages"]
    name = next(iter(pages.values())).get("pageimage")
    if not name:
        raise RuntimeError(f"{article} has no lead image")
    return name


def image_info(filename):
    """Download URL and credit details for a file, from Commons or en.wikipedia."""
    for host in ("commons.wikimedia.org", "en.wikipedia.org"):
        api = (f"https://{host}/w/api.php?action=query&format=json&prop=imageinfo"
               f"&iiprop=url|extmetadata&iiurlwidth={WIDTH}&titles=" + urllib.parse.quote("File:" + filename))
        pages = get_json(api)["query"]["pages"]
        page = next(iter(pages.values()))
        if "imageinfo" in page:
            info = page["imageinfo"][0]
            meta = info.get("extmetadata", {})
            return {
                "url": info.get("thumburl") or info["url"],
                "page": info["descriptionurl"],
                "artist": strip_tags(meta.get("Artist", {}).get("value")),
                "licence": strip_tags(meta.get("LicenseShortName", {}).get("value")),
                "licence_url": meta.get("LicenseUrl", {}).get("value", ""),
            }
    raise RuntimeError(f"No image information for {filename}")


def credit_html(info):
    parts = []
    if info["artist"] and info["artist"].lower() not in ("unknown", "unknown author"):
        parts.append(html.escape(info["artist"]))
    if info["licence"]:
        licence = html.escape(info["licence"])
        if info["licence_url"]:
            licence = f'<a href="{html.escape(info["licence_url"])}">{licence}</a>'
        parts.append(licence)
    parts.append(f'via <a href="{html.escape(info["page"])}">Wikimedia Commons</a>')
    return ", ".join(parts[:-1]) + (" " if len(parts) > 1 else "") + parts[-1] + "."


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    page = ABOUT.read_text()
    for key, article in PEOPLE.items():
        filename = lead_image(article)
        info = image_info(filename)
        image = Image.open(io.BytesIO(get(info["url"]))).convert("RGB")
        if image.width > WIDTH:
            image = image.resize((WIDTH, round(image.height * WIDTH / image.width)), Image.LANCZOS)
        image.save(OUT / f"{key}.jpg", "JPEG", quality=88, optimize=True, progressive=True)

        credit = credit_html(info)
        page, found = re.subn(
            rf'(<span class="credit" data-portrait="{key}">).*?(</span>)',
            lambda m: m.group(1) + credit + m.group(2),
            page,
            flags=re.S,
        )
        if not found:
            print(f"warning: no caption for {key} in {ABOUT}", file=sys.stderr)
        print(f"{key}: {filename} ({image.width}x{image.height}) - {strip_tags(credit)}")
    ABOUT.write_text(page)
    print("Done. Check the portraits and captions, then commit site/img/portraits and site/about.html.")


if __name__ == "__main__":
    main()
