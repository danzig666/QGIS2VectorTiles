// Simple HTML of long published texts (a zone's full regulations): the same
// rules as the plugin's rich_text.py, applied again here. The text is parsed
// by DOMParser (an inert document: nothing runs or loads) and rebuilt from
// the allowed elements only, without attributes (a table cell keeps a
// numeric colspan/rowspan); other elements leave their text, scripts and
// embedded content go whole.
const ALLOWED = new Set(["h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "strong", "em", "b", "i", "u", "br",
  "sup", "sub", "table", "thead", "tbody", "tr", "th", "td", "blockquote", "hr"]);
const RENAMED = { h1: "h3", h2: "h3" };
const DROPPED = new Set(["script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math",
  "head", "title", "textarea", "select", "button", "form"]);

function copy(source, target) {
  for (const node of source.childNodes) {
    if (node.nodeType === 3) {
      target.append(document.createTextNode(node.nodeValue));
      continue;
    }
    if (node.nodeType !== 1) continue;
    const name = node.nodeName.toLowerCase();
    if (DROPPED.has(name)) continue;
    const tag = RENAMED[name] || name;
    if (!ALLOWED.has(tag)) {
      copy(node, target);  // the text, without the element
      continue;
    }
    const element = document.createElement(tag);
    if (tag === "td" || tag === "th") {
      for (const span of ["colspan", "rowspan"]) {
        const value = node.getAttribute(span);
        if (value && /^\d{1,2}$/.test(value) && +value >= 1 && +value <= 50) element.setAttribute(span, value);
      }
    }
    copy(node, element);
    target.append(element);
  }
}

// A <div class="q2vt-rich"> with the cleaned text.
export function richText(html) {
  const box = document.createElement("div");
  box.className = "q2vt-rich";
  const parsed = new DOMParser().parseFromString(`<body>${String(html || "")}</body>`, "text/html");
  copy(parsed.body, box);
  return box;
}
