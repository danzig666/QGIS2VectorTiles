// Hungarian edition (hu-hesz) viewer add-on: a zone's popup lists its
// regulations - the parcel report's regulation table (HÉSZ övezeti
// előírások) joined by the zone code - with links to the decree (a URL, or
// a document published with the map). Installed by controls.mjs from
// manifest.addons; it brings its own strings and styles.
import { formatValue } from "../identify.mjs";
import { zoneKey } from "../parcel_report.mjs";

const TEXT = { hu: "A(z) {code} övezet előírásai", en: "Regulations of zone {code}" };
const STYLE = `.q2vt-zone-rules { margin-top: 8px; border-top: 1px solid var(--q2vt-border); padding-top: 6px; }
.q2vt-zone-rules h4 { margin: 0 0 4px; font-size: 12.5px; color: var(--q2vt-accent); }
.q2vt-zone-rules .q2vt-rich-block { margin-top: 6px; }
.q2vt-zone-rules .q2vt-rich { max-height: 40vh; }`;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// The rows [title, text, url] of a zone code's regulations.
export function zoneRules(catalog, code) {
  // The table's keys: trimmed text, whole numbers without ".0" (zone_key).
  const regulation = ((catalog && catalog.regulations) || {})[zoneKey(code)];
  if (!regulation) return [];
  const rows = [];
  for (const field of catalog.regulationFields || []) {
    const raw = regulation[field.field];
    if (raw === null || raw === undefined || raw === "") continue;
    const shown = formatValue(raw, field.type);
    rows.push([field.title || field.field, shown.text, shown.url || null]);
  }
  return rows;
}

export function install({ identify, parcel, manifest }) {
  const info = manifest.parcelInfo;
  if (!identify || !parcel || !info || !info.zoningLayerId || !info.zoneCodeField) return false;
  const style = document.createElement("style");
  style.textContent = STYLE;
  document.head.append(style);
  const title = (code) => (TEXT[manifest.locale] || TEXT.en).replace("{code}", String(code));
  identify.extend = async (box, { layerId, record }) => {
    if (layerId !== info.zoningLayerId || !record || !record.a) return;
    const code = record.a[info.zoneCodeField];
    if (!zoneKey(code)) return;
    await parcel.load();
    const rows = zoneRules(parcel.catalog, code);
    // The zone's full regulation text, opened on request (parcel_report.mjs).
    const full = parcel.regulationBlock ? parcel.regulationBlock(code) : null;
    if (!rows.length && !full) return;
    const section = element("section", "q2vt-zone-rules");
    section.append(element("h4", "", title(code)));
    const table = element("table");
    for (const [name, text, url] of rows) {
      const row = element("tr");
      row.append(element("th", "", name));
      const cell = element("td");
      if (url) {
        const link = element("a", "", text);
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        cell.append(link);
      } else cell.textContent = text;
      row.append(cell);
      table.append(row);
    }
    if (rows.length) section.append(table);
    if (full) section.append(full);
    box.append(section);
  };
  return true;
}
