// Draws the QGIS diagrams (pie, histogram, stacked bar, text) of a published
// map. The style names each feature's diagram image after its size and
// category values ("q2vt-diagram|<style layer>|<size>|<v1;v2;...>", see
// src/core/fidelity/diagrams.py); MapLibre asks for each image it misses
// (styleimagemissing) and this draws it from the layer's settings
// (metadata "q2vt:diagram") on a canvas, as QGIS draws the diagram:
// sizes in CSS px (``px`` per diagram unit, ``linePx`` per pen unit).

export const PREFIX = "q2vt-diagram|";
const MAX_CSS = 512; // largest image side (CSS px)

export function diagramSpecs(style) {
  const specs = new Map();
  for (const layer of (style && style.layers) || []) {
    const spec = layer.metadata && layer.metadata["q2vt:diagram"];
    if (spec) specs.set(spec.id, spec);
  }
  return specs;
}

// "<prefix><id>|<size>|<values>" -> {id, size, values} (null: not a diagram).
export function parseName(name) {
  if (typeof name !== "string" || !name.startsWith(PREFIX)) return null;
  const rest = name.slice(PREFIX.length);
  const last = rest.lastIndexOf("|");
  const middle = last > 0 ? rest.lastIndexOf("|", last - 1) : -1;
  if (middle < 0) return null;
  const values = rest.slice(last + 1).split(";").filter((v) => v !== "").map((v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  });
  return { id: rest.slice(0, middle), size: Number(rest.slice(middle + 1, last)) || 0, values };
}

// The diagram's box (CSS px) and how to draw it.
export function layout(spec, size, values) {
  const px = spec.px || 1;
  const n = values.length;
  const bar = Math.max(spec.barWidth * px, 0);
  const spacing = Math.max((spec.spacing || 0) * px, 0);
  const vertical = spec.orientation === "up" || spec.orientation === "down";
  if (spec.type === "histogram") {
    const vmax = Math.max(0, ...values);
    // QGIS: an interpolated diagram scales by its value span, a fixed one by
    // the feature's largest value over the size (QgsHistogramDiagram).
    const factor = spec.interpolated ? (spec.scale || 0) : (spec.height ? vmax / spec.height : 0);
    const lengths = values.map((v) => Math.max(v, 0) * factor * px);
    const across = n * bar + Math.max(n - 1, 0) * spacing;
    const along = Math.max(0, ...lengths);
    return { kind: "histogram", lengths, bar, spacing, vertical,
      width: vertical ? across : along, height: vertical ? along : across };
  }
  if (spec.type === "stacked") {
    const total = (spec.interpolated ? size : spec.height) * px;
    const sum = values.reduce((a, v) => a + Math.max(v, 0), 0);
    const lengths = values.map((v) => (sum > 0 ? Math.max(v, 0) / sum * total : 0));
    return { kind: "stacked", lengths, bar, spacing, vertical,
      width: vertical ? bar : total, height: vertical ? total : bar };
  }
  const width = size * px;
  return { kind: spec.type, width, height: width * (spec.aspect || 1) };
}

function pen(ctx, spec) {
  const width = (spec.penWidth || 0) * (spec.linePx || spec.px || 1);
  ctx.lineWidth = width;
  ctx.strokeStyle = spec.penColor;
  return width > 0;
}

function drawPie(ctx, spec, values, w, h, stroke) {
  const sum = values.reduce((a, v) => a + Math.max(v, 0), 0);
  if (!(sum > 0)) return;
  const sign = spec.clockwise ? 1 : -1;
  let angle = (spec.rotation || 0) * Math.PI / 180; // clockwise from 3 o'clock
  for (let i = 0; i < values.length; i++) {
    const sweep = Math.max(values[i], 0) / sum * 2 * Math.PI * sign;
    if (sweep === 0) continue;
    ctx.beginPath();
    ctx.moveTo(w / 2, h / 2);
    ctx.ellipse(w / 2, h / 2, w / 2, h / 2, 0, angle, angle + sweep, sign < 0);
    ctx.closePath();
    ctx.fillStyle = spec.colors[i] || "#888";
    ctx.fill();
    if (stroke) ctx.stroke();
    angle += sweep;
  }
}

function drawBars(ctx, spec, box, stroke) {
  const { lengths, bar, spacing } = box;
  const W = box.width;
  const H = box.height;
  let offset = 0; // along the bars (stacked) or across them (histogram)
  for (let i = 0; i < lengths.length; i++) {
    const length = lengths[i];
    const step = box.kind === "stacked" ? 0 : i * (bar + spacing);
    const start = box.kind === "stacked" ? offset : 0;
    let x;
    let y;
    let w;
    let h;
    switch (spec.orientation) {
      case "down": [x, y, w, h] = [step, start, bar, length]; break;
      case "left": [x, y, w, h] = [W - start - length, step, length, bar]; break;
      case "right": [x, y, w, h] = [start, step, length, bar]; break;
      default: [x, y, w, h] = [step, H - start - length, bar, length]; // up
    }
    offset += length;
    if (!(w > 0 && h > 0)) continue;
    ctx.fillStyle = spec.colors[i] || "#888";
    ctx.fillRect(x, y, w, h);
    if (stroke) ctx.strokeRect(x, y, w, h);
  }
}

function drawText(ctx, spec, values, w, h, stroke) {
  ctx.beginPath();
  ctx.ellipse(w / 2, h / 2, w / 2, h / 2, 0, 0, 2 * Math.PI);
  ctx.fillStyle = spec.background;
  ctx.fill();
  if (stroke) ctx.stroke();
  const lines = values.length || 1;
  const font = Math.max(6, Math.min(h / (lines + 1), w / 4));
  ctx.font = `${font}px sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  values.forEach((v, i) => {
    ctx.fillStyle = spec.colors[i] || "#000";
    ctx.fillText(String(Math.round(v * 100) / 100), w / 2, h / 2 + (i - (lines - 1) / 2) * font * 1.1);
  });
}

// RGBA pixels of one diagram image: {width, height, data, pixelRatio}.
export function renderDiagram(spec, size, values, ratio = 1, document_ = globalThis.document) {
  const box = layout(spec, size, values);
  const strokeWidth = (spec.penWidth || 0) * (spec.linePx || spec.px || 1);
  const margin = Math.ceil(strokeWidth / 2) + 1;
  const scale = Math.min(1, MAX_CSS / Math.max(box.width, box.height, 1));
  const w = Math.max(box.width * scale, 1);
  const h = Math.max(box.height * scale, 1);
  const width = Math.max(1, Math.ceil((w + 2 * margin) * ratio));
  const height = Math.max(1, Math.ceil((h + 2 * margin) * ratio));
  const canvas = document_.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.translate(margin, margin);
  const stroke = pen(ctx, spec);
  if (box.kind === "pie") {
    drawPie(ctx, spec, values, w, h, stroke);
  } else if (box.kind === "text") {
    drawText(ctx, spec, values, w, h, stroke);
  } else {
    ctx.scale(scale, scale);
    if (stroke) ctx.lineWidth = strokeWidth / scale;
    drawBars(ctx, spec, box, stroke);
  }
  const data = ctx.getImageData(0, 0, width, height).data;
  return { width, height, data: new Uint8Array(data.buffer), pixelRatio: ratio };
}

// Draws the diagram images MapLibre asks for. Registered before the style
// loads so the first frame already has them.
export function installDiagrams(map, style) {
  const specs = diagramSpecs(style);
  if (!specs.size) return null;
  const ratio = Math.min(Math.max(Math.ceil(window.devicePixelRatio || 1), 1), 3);
  const drawn = new Set();
  const draw = (id) => {
    const parsed = parseName(id);
    const spec = parsed && specs.get(parsed.id);
    if (!spec || drawn.has(id) || map.hasImage(id)) return;
    try {
      const image = renderDiagram(spec, parsed.size, parsed.values, ratio);
      map.addImage(id, { width: image.width, height: image.height, data: image.data },
        { pixelRatio: image.pixelRatio });
      drawn.add(id);
    } catch (error) {
      console.warn("q2vt: diagram", id, error); // eslint-disable-line no-console
    }
  };
  // The resolver (MapLibre 6.12+) answers the tile's request with the image;
  // the styleimagemissing event comes after the request was answered without
  // it (a console warning, the diagram drawn only with a later tile).
  if (typeof map.setMissingStyleImageResolver === "function") {
    map.setMissingStyleImageResolver((id) => draw(id));
  } else {
    map.on("styleimagemissing", (event) => draw(event.id));
  }
  return { specs, count: () => drawn.size };
}
