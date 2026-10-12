// Error states of the viewer: distinct, translated, never replaced by an
// image or a GeoJSON copy of the map. Text goes in with textContent only.
import { t } from "./i18n.mjs";

const MESSAGES = {
  Q2VT_PUB_WEBGL: "error.webgl",
  Q2VT_PUB_MANIFEST: "error.manifest",
  Q2VT_PUB_SCHEMA_UNSUPPORTED: "error.schema",
  Q2VT_PUB_RANGE_UNSUPPORTED: "error.range",
  Q2VT_PUB_CORS: "error.cors",
  Q2VT_PUB_NOT_MVT: "error.notMvt",
  Q2VT_PUB_PMTILES_INVALID: "error.archive",
  Q2VT_PUB_RASTER_SOURCE: "error.notMvt",
  Q2VT_PUB_STYLE: "error.style",
  Q2VT_PUB_PATH_UNSAFE: "error.manifest",
  Q2VT_PUB_FETCH: "error.fetch",
  Q2VT_PUB_DEPENDENCY: "error.fetch",
};

export const state = { errors: [], warnings: [] };

export function showError(error) {
  const code = (error && error.code) || "Q2VT_PUB_FETCH";
  const detail = (error && (error.detail || error.message)) || String(error);
  state.errors.push({ code, detail });
  const box = document.getElementById("q2vt-error");
  if (!box) return;
  box.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = t("error.title");
  const text = document.createElement("p");
  text.textContent = t(MESSAGES[code] || "error.fetch");
  const more = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = t("error.detail");
  const pre = document.createElement("pre");
  pre.textContent = `${code}\n${String(detail).slice(0, 2000)}`;
  more.append(summary, pre);
  box.append(title, text, more);
  box.hidden = false;
  document.body.classList.add("q2vt-failed");
}

export function warn(key, detail = "") {
  state.warnings.push({ key, detail: String(detail).slice(0, 500) });
  const bar = document.getElementById("q2vt-warning");
  if (!bar) return;
  const item = document.createElement("div");
  item.textContent = t(key);
  bar.append(item);
  bar.hidden = false;
}

// A fetch() that classifies failures (HTTP status vs. network/CORS).
export async function fetchChecked(url, kind = "json") {
  let response;
  try {
    response = await fetch(url, { credentials: "same-origin" });
  } catch (error) {
    const sameOrigin = new URL(url, location.href).origin === location.origin;
    throw Object.assign(new Error(`${url}: ${error.message}`), { code: sameOrigin ? "Q2VT_PUB_FETCH" : "Q2VT_PUB_CORS" });
  }
  if (!response.ok) {
    throw Object.assign(new Error(`${url}: HTTP ${response.status}`), { code: "Q2VT_PUB_FETCH" });
  }
  return kind === "json" ? response.json() : response.text();
}
