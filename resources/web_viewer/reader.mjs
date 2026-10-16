// A large reading window for long texts (a zone's full regulations): the
// text at a readable width over the map, larger or smaller type, and the
// text alone printed or saved as an HTML file. The content is the cleaned
// text of rich_text.mjs (no scripts, no attributes but table spans); it is
// copied, never parsed again from strings.
import { BackEntry } from "./backstack.mjs";
import { t } from "./i18n.mjs";
import { button, el } from "./icons.mjs";

const SIZES = [14, 16, 18, 21, 24];
const PAGE_STYLE = `body { font: 11pt/1.5 Georgia, "Times New Roman", serif; color: #111; margin: 0; }
main { max-width: 46em; margin: 0 auto; padding: 2em 1.5em; }
header { border-bottom: 1px solid #999; margin-bottom: 1em; padding-bottom: .5em; }
header h1 { font-size: 1.35em; margin: 0; }
header p { margin: .2em 0 0; color: #555; font-size: .9em; }
h3, h4, h5, h6 { break-after: avoid; margin: 1em 0 .3em; }
ul { list-style: none; padding-left: 1.2em; } ol { padding-left: 1.6em; }
table { border-collapse: collapse; margin: .5em 0; } th, td { border: 1px solid #999; padding: 2px 6px; vertical-align: top; text-align: left; }
li, tr { break-inside: avoid; } hr { border: 0; border-top: 1px solid #999; margin: 1em 0; }
blockquote { margin: .4em 0; padding-left: .8em; border-left: 3px solid #ccc; }
@page { margin: 18mm 16mm; } @media print { main { max-width: none; padding: 0; } }`;

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// A standalone HTML page with the title, subtitle and the text.
export function readerPage({ title, subtitle, content, lang }) {
  const body = content ? content.innerHTML : "";
  return `<!doctype html>\n<html lang="${escapeHtml(lang || "en")}"><head><meta charset="utf-8">`
    + `<meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(title)}</title>`
    + `<style>${PAGE_STYLE}</style></head><body><main><header><h1>${escapeHtml(title)}</h1>`
    + (subtitle ? `<p>${escapeHtml(subtitle)}</p>` : "") + `</header>${body}</main></body></html>\n`;
}

// "A(z) L-1 övezet ..." -> "a-z-l-1-ovezet-....html"
export function fileName(title) {
  const slug = String(title || "text").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
  return `${slug || "text"}.html`;
}

function download(page, name) {
  const url = URL.createObjectURL(new Blob([page], { type: "text/html;charset=utf-8" }));
  const link = el("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// Prints the page alone from a hidden frame (the map's own print layout is
// left alone).
function printPage(page) {
  const url = URL.createObjectURL(new Blob([page], { type: "text/html;charset=utf-8" }));
  const frame = el("iframe", "q2vt-reader-frame");
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  frame.addEventListener("load", () => {
    try {
      frame.contentWindow.focus();
      frame.contentWindow.print();
    } finally {
      setTimeout(() => { frame.remove(); URL.revokeObjectURL(url); }, 60000);
    }
  }, { once: true });
  frame.src = url;
  document.body.append(frame);
}

let open = null;

// Opens the reading window; ``content`` is a .q2vt-rich element (copied).
export function openReader({ title, subtitle, content, lang }) {
  if (open) open.close();
  const previous = document.activeElement;
  const backdrop = el("div", "q2vt-reader-backdrop");
  const dialog = el("div", "q2vt-reader");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", title);
  const head = el("header", "q2vt-reader-head");
  const names = el("div", "q2vt-reader-titles");
  names.append(el("h2", "", title));
  if (subtitle) names.append(el("p", "", subtitle));
  const tools = el("div", "q2vt-reader-tools");
  const smaller = button("q2vt-chip q2vt-reader-size", t("reader.smaller"), null, { text: "A−" });
  const larger = button("q2vt-chip q2vt-reader-size", t("reader.larger"), null, { text: "A+" });
  const print = button("q2vt-chip", t("reader.print"), "print", { text: t("reader.print") });
  const save = button("q2vt-chip", t("reader.save"), "download", { text: t("reader.save") });
  const close = button("q2vt-icon-btn q2vt-reader-close", t("reader.close"), "close");
  tools.append(smaller, larger, print, save, close);
  head.append(names, tools);
  const body = el("div", "q2vt-reader-body");
  const text = content.cloneNode(true);
  text.classList.add("q2vt-reader-text");
  body.append(text);
  dialog.append(head, body);
  backdrop.append(dialog);

  let size = 1;
  const applySize = () => {
    text.style.fontSize = `${SIZES[size]}px`;
    smaller.disabled = size === 0;
    larger.disabled = size === SIZES.length - 1;
  };
  applySize();
  smaller.addEventListener("click", () => { size = Math.max(0, size - 1); applySize(); });
  larger.addEventListener("click", () => { size = Math.min(SIZES.length - 1, size + 1); applySize(); });
  const page = () => readerPage({ title, subtitle, content, lang });
  print.addEventListener("click", () => printPage(page()));
  save.addEventListener("click", () => download(page(), fileName(title)));

  const onKey = (event) => {
    if (event.key === "Escape") { event.stopPropagation(); finish(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button:not([disabled]), a[href], [tabindex='0']")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  const back = new BackEntry(() => finish());
  function finish() {
    back.closed();
    document.removeEventListener("keydown", onKey, true);
    backdrop.remove();
    document.body.classList.remove("q2vt-reading");
    open = null;
    if (previous && previous.focus) previous.focus();
  }
  close.addEventListener("click", finish);
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) finish(); });
  document.addEventListener("keydown", onKey, true);
  document.body.append(backdrop);
  document.body.classList.add("q2vt-reading");
  back.open();
  body.tabIndex = 0;  // scrollable with the keyboard
  close.focus();
  open = { close: finish, dialog };
  return open;
}
