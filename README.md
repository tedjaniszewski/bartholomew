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
  js/search.js        place-name autocomplete (waits on the OCR gazetteer)
  data/maps.json      registry of maps the site can show
  fonts/charter/      Bitstream Charter webfonts
  vendor/             OpenSeadragon 5.0.1
  iiif/               generated tiles (not committed)
tools/
  make_iiif_tiles.py  turn one image into static IIIF Image API 3.0 tiles
  build_tiles.py      run it for every map in maps.json
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

### Shareable views

The URL hash records the map and view, e.g.
`#map=palestine-1901&at=3334,5000,0.84` (centre x, centre y in sheet pixels,
and magnification relative to the scan).
