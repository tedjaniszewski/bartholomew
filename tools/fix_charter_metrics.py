"""
The Charter webfonts from the charter-webfont package (v4.1.0) store their
descent as a positive number in the hhea and OS/2 tables, where the spec
requires a negative one. Browsers then compute a line box that is far too
short, and text in form fields is clipped. This flips the sign in place.

    python3 tools/fix_charter_metrics.py site/fonts/charter/*.woff2
"""

import sys

from fontTools.ttLib import TTFont

for path in sys.argv[1:]:
    font = TTFont(path)
    hhea, os2 = font["hhea"], font["OS/2"]
    hhea.descent = -abs(hhea.descent)
    os2.sTypoDescender = -abs(os2.sTypoDescender)
    font.flavor = "woff2"
    font.save(path)
    print(f"{path}: hhea descent {hhea.descent}, typo descender {os2.sTypoDescender}")
