# Bartholomew

An interactive, deep-zoom edition of J. G. Bartholomew's *Topographical and
Physical Map of Palestine* (Edinburgh, 1901), edited by George Adam Smith and
compiled from the Palestine Exploration Fund surveys.

The project has two branches:

1. **The restored image.** Combining the available scans (Geographicus,
   RareMaps, Berkeley) with new photography of a physical copy and of the
   plates in Smith's *Historical Geography of the Holy Land*, to produce one
   crease-free master image. Source scans live in `geographicus/`,
   `raremaps/` and `berkeley/`.
2. **The website**, in `site/`. It currently shows the Geographicus scan.

## The website

A static site with no build step beyond generating image tiles.

```
site/
  index.html          map viewer
  about.html          about the map
  css/bartholomew.css colours, type and layout
  js/app.js           start-up, map switcher, info bar, shareable URLs
  js/viewer.js        wrapper around OpenSeadragon (all map access goes through it)
  js/search.js        place-name autocomplete over the map's gazetteer
  review.html         place-name review tool (js/review.js, css/review.css)
  data/maps.json      registry of maps the site can show
  data/gazetteers/    one place-name index per map
  fonts/charter/      Bitstream Charter webfonts
  vendor/             OpenSeadragon 5.0.1
  iiif/               generated tiles (not committed)
tools/
  make_iiif_tiles.py  turn one image into static IIIF Image API 3.0 tiles
  build_tiles.py      run it for every map in maps.json
  review_server.py    local server that lets the review tool save
  ocr/run_ocr.py      read all text on a map (OnnxTR + RapidOCR)
  ocr/build_gazetteer.py  group OCR words into labels for review
ocr/<map-id>/
  config.json         insets, margins etc. on that sheet
  words.json          raw OCR output
```

### Run it locally

```sh
pip install pillow
python3 tools/build_tiles.py
python3 -m http.server -d site 8000
```

Then open <http://localhost:8000>.

### Deploying

`.github/workflows/pages.yml` builds the tiles and publishes `site/` to GitHub
Pages on every push to `main`. In the repository settings, under
**Pages → Build and deployment**, set the source to **GitHub Actions**.

### Adding a map

Add an entry to `site/data/maps.json`:

```json
{
  "id": "smith-atlas-1915-plate-3",
  "title": "…",
  "shortTitle": "…",
  "date": "1915",
  "credit": "…",
  "image": "iiif/smith-atlas-1915-plate-3/info.json",
  "build": { "source": "path/to/master-image.tif", "trim": false },
  "scan": { "name": "…", "url": "…" },
  "georeference": null,
  "gazetteer": null
}
```

`build_tiles.py` generates its tiles, and the map appears in the switcher
under the title bar. `georeference` (control points linking sheet pixels to
latitude and longitude) and `gazetteer` (the OCR place-name index) are
placeholders for the coming features.

## The place-name index

Each map has its own gazetteer, `site/data/gazetteers/<map-id>.json`, named
by the `gazetteer` field in `maps.json`; switching maps switches indexes.
Every label printed on the sheet is an entry:

```json
{"id":"l00412","name":"Beth-shean","alt":["Beisan"],"kind":"place",
 "box":[3412,4870,96,18],"status":"confirmed","reviewed":true,
 "ocr":{"text":"Beth-shean","conf":0.98,"alt":"Bethshean"}}
```

`box` is the label's position in sheet pixels. `status` is `unreviewed`,
`confirmed`, `rejected`, or `merged` (joined into another entry, `into`).
Kinds include `place`, `ruin`, `wadi`, `mountain`, `height` (spot
elevations, for the future elevation readout) and `graticule` (margin
degree figures, for georeferencing). The public search shows confirmed and
unreviewed places, not heights, margins or legends.

### Building an index

```sh
pip install "onnxtr[cpu]" rapidocr-onnxruntime pillow
python3 tools/ocr/run_ocr.py palestine-1901        # ~15 minutes on 4 cores
python3 tools/ocr/build_gazetteer.py palestine-1901
```

Rerunning `build_gazetteer.py` (say, after improving the grouping rules)
keeps every entry already touched in review and only adds new OCR labels
that don't overlap them.

### Reviewing

```sh
python3 tools/review_server.py
```

Open <http://localhost:8000/review.html>. Each correction is saved straight
to the gazetteer file; commit it to publish. Press **Keys** in the tool for
the shortcuts. The tool also works on the published site
(`/review.html`), where changes are kept in that browser and moved to the
repository with **Export**.

### When the restored master image replaces the scan

Gazetteer boxes are in the pixel space of the image they were read from
(`source` in the gazetteer). When a new image replaces it, the boxes will be
carried across with the same alignment used to register the scans, so the
review work is kept.

### Shareable views

The URL hash records the map and view, e.g.
`#map=palestine-1901&at=3334,5000,0.84` (centre x, centre y in sheet pixels,
and magnification relative to the scan).
