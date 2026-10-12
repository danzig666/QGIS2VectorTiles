// Elevation profile of a measured line: evenly spaced samples along it,
// heights from the terrain tiles (threed.mjs elevations), drawn as a small
// SVG chart with the lowest and highest point, total climb and descent.
import { t, formatNumber } from "./i18n.mjs";
import { el } from "./icons.mjs";

const R = 6371008.8;

function metres(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad, dLng = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

// [{distance, point}] every length/count metres along the line (both ends included).
export function sampleLine(points, count = 200) {
  const legs = points.slice(1).map((p, i) => metres(points[i], p));
  const total = legs.reduce((a, b) => a + b, 0);
  const out = [];
  let leg = 0, start = 0;
  for (let i = 0; i <= count; i++) {
    const at = (total * i) / count;
    while (leg < legs.length - 1 && start + legs[leg] < at) { start += legs[leg]; leg += 1; }
    const f = legs[leg] ? Math.min(1, (at - start) / legs[leg]) : 0;
    const a = points[leg], b = points[leg + 1] || a;
    out.push({ distance: at, point: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f] });
  }
  return { samples: out, total };
}

export function profileStats(heights) {
  const known = heights.filter(Number.isFinite);
  if (!known.length) return null;
  let up = 0, down = 0, last = null;
  for (const h of heights) {
    if (!Number.isFinite(h)) continue;
    if (last !== null) { if (h > last) up += h - last; else down += last - h; }
    last = h;
  }
  return { min: Math.min(...known), max: Math.max(...known), up, down };
}

export function renderProfile(container, samples, heights) {
  container.replaceChildren();
  const stats = profileStats(heights);
  if (!stats) {
    container.append(el("p", "q2vt-muted", t("profile.noData")));
    return null;
  }
  const W = 320, H = 150, L = 42, B = 22, T = 8, Rm = 8;
  const total = samples[samples.length - 1].distance || 1;
  const span = Math.max(1, stats.max - stats.min);
  const low = stats.min - span * 0.08, high = stats.max + span * 0.08;
  const x = (d) => L + ((W - L - Rm) * d) / total;
  const y = (h) => T + (H - T - B) * (1 - (h - low) / (high - low));
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "q2vt-profile-chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", t("profile.title"));
  const add = (tag, attrs, text) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    svg.append(node);
    return node;
  };
  let line = "", area = "";
  samples.forEach((s, i) => {
    if (!Number.isFinite(heights[i])) return;
    const cmd = line ? "L" : "M";
    line += `${cmd}${x(s.distance).toFixed(1)},${y(heights[i]).toFixed(1)}`;
  });
  area = `${line}L${x(total).toFixed(1)},${H - B}L${x(0).toFixed(1)},${H - B}Z`;
  for (const h of [stats.min, stats.max]) {
    add("line", { x1: L, x2: W - Rm, y1: y(h), y2: y(h), class: "q2vt-profile-grid" });
    add("text", { x: L - 4, y: y(h) + 3, "text-anchor": "end", class: "q2vt-profile-label" }, `${formatNumber(h, 0)} m`);
  }
  add("path", { d: area, class: "q2vt-profile-area" });
  add("path", { d: line, class: "q2vt-profile-line" });
  add("text", { x: L, y: H - 6, class: "q2vt-profile-label" }, "0");
  add("text", { x: W - Rm, y: H - 6, "text-anchor": "end", class: "q2vt-profile-label" },
    total >= 1000 ? `${formatNumber(total / 1000, 2)} km` : `${formatNumber(total, 0)} m`);
  const facts = el("p", "q2vt-profile-facts", t("profile.facts", {
    min: formatNumber(stats.min, 0), max: formatNumber(stats.max, 0),
    up: formatNumber(stats.up, 0), down: formatNumber(stats.down, 0) }));
  container.append(svg, facts);
  return stats;
}
