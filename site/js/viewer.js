// A thin wrapper around OpenSeadragon. Everything else in the site talks to
// the map through this class, in image pixel coordinates, so the viewer
// library could be swapped without touching the rest of the code.

const OSD = window.OpenSeadragon;

const PIN_SVG = `
  <svg class="pin" viewBox="0 0 22 30" aria-hidden="true">
    <path d="M11 29C11 29 2 17.5 2 10.5a9 9 0 0 1 18 0C20 17.5 11 29 11 29z"/>
    <circle cx="11" cy="10.5" r="3.4"/>
  </svg>`;

export class MapViewer {
  constructor(element, { onSelect, onZoom, onViewChange } = {}) {
    this.onSelect = onSelect;
    this.onZoom = onZoom;
    this.onViewChange = onViewChange;
    this.pin = null;
    this.highlightEl = null;

    const wide = window.matchMedia("(min-width: 761px)").matches;

    this.osd = OSD({
      element,
      showNavigationControl: false,
      showNavigator: wide,
      navigatorPosition: "TOP_RIGHT",
      navigatorSizeRatio: 0.13,
      navigatorAutoFade: false,
      navigatorBackground: "transparent",
      navigatorBorderColor: "transparent",
      navigatorDisplayRegionColor: "#b23a2e",
      // Allow zooming past the scan's native resolution, so small lettering
      // can be read comfortably even on high-density screens.
      maxZoomPixelRatio: 4,
      minZoomImageRatio: 0.8,
      visibilityRatio: 0.6,
      constrainDuringPan: true,
      animationTime: 0.9,
      springStiffness: 8,
      immediateRender: false,
      preserveImageSizeOnResize: true,
      gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true, scrollToZoom: true },
      gestureSettingsTouch: { clickToZoom: false, dblClickToZoom: true, pinchRotate: false, flickEnabled: true },
      gestureSettingsPen: { clickToZoom: false, dblClickToZoom: true },
    });

    this.osd.addHandler("canvas-click", (event) => {
      // "quick" distinguishes a tap or click from the end of a drag.
      if (!event.quick || !this.onSelect) return;
      const point = this.imagePointFromPixel(event.position);
      if (point) this.onSelect(point);
    });

    this.osd.addHandler("animation-finish", () => this.onViewChange?.(this.getView()));
    this.osd.addHandler("zoom", () => this.onZoom?.(this.getView()));
  }

  // Open a map from its IIIF info.json URL. Static (level 0) tiles cannot know
  // the address they will be served from, so the "id" is rewritten here to
  // the folder holding info.json.
  async open(infoUrl, view) {
    const url = new URL(infoUrl, document.baseURI);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`);
    const info = await response.json();
    info.id = url.href.replace(/\/info\.json$/, "");
    if ("@id" in info) info["@id"] = info.id;

    this.clearPin();
    this.clearHighlight();

    await new Promise((resolve, reject) => {
      const off = () => {
        this.osd.removeHandler("open", onOpen);
        this.osd.removeHandler("open-failed", onFail);
      };
      const onOpen = () => { off(); resolve(); };
      const onFail = (e) => { off(); reject(new Error(e.message || "Could not open the map")); };
      this.osd.addHandler("open", onOpen);
      this.osd.addHandler("open-failed", onFail);
      this.osd.open(info);
    });

    this.size = { width: info.width, height: info.height };
    if (view) this.setView(view, true);
  }

  imagePointFromPixel(pixel) {
    const viewportPoint = this.osd.viewport.pointFromPixel(pixel);
    const p = this.osd.viewport.viewportToImageCoordinates(viewportPoint);
    if (p.x < 0 || p.y < 0 || p.x > this.size.width || p.y > this.size.height) return null;
    return { x: p.x, y: p.y };
  }

  zoomBy(factor) {
    this.osd.viewport.zoomBy(factor);
    this.osd.viewport.applyConstraints();
  }

  goHome() {
    this.osd.viewport.goHome();
  }

  // The view, in image pixels: the centre point and the image zoom
  // (1 = one screen pixel per scan pixel).
  getView() {
    const vp = this.osd.viewport;
    const center = vp.viewportToImageCoordinates(vp.getCenter(true));
    return { x: center.x, y: center.y, zoom: vp.viewportToImageZoom(vp.getZoom(true)) };
  }

  setView({ x, y, zoom }, immediately = false) {
    const vp = this.osd.viewport;
    vp.zoomTo(vp.imageToViewportZoom(zoom), null, immediately);
    vp.panTo(vp.imageToViewportCoordinates(x, y), immediately);
    vp.applyConstraints(immediately);
  }

  showPin({ x, y }) {
    this.clearPin();
    const el = document.createElement("div");
    el.innerHTML = PIN_SVG.trim();
    this.pin = el.firstChild;
    this.osd.addOverlay({
      element: this.pin,
      location: this.osd.viewport.imageToViewportCoordinates(x, y),
      placement: OSD.Placement.BOTTOM,
      checkResize: false,
    });
  }

  clearPin() {
    if (this.pin) this.osd.removeOverlay(this.pin);
    this.pin = null;
  }

  // Zoom to a rectangle of the image (e.g. a place-name label found by the
  // search) and outline it.
  highlight({ x, y, width, height }) {
    this.clearHighlight();
    const vp = this.osd.viewport;
    const rect = vp.imageToViewportRectangle(x, y, width, height);
    this.highlightEl = document.createElement("div");
    this.highlightEl.className = "label-highlight";
    this.osd.addOverlay({ element: this.highlightEl, location: rect });

    // Leave generous context around the label rather than filling the screen.
    const pad = Math.max(rect.width, rect.height) * 6;
    vp.fitBounds(new OSD.Rect(rect.x - pad, rect.y - pad, rect.width + 2 * pad, rect.height + 2 * pad));
  }

  clearHighlight() {
    if (this.highlightEl) this.osd.removeOverlay(this.highlightEl);
    this.highlightEl = null;
  }
}
