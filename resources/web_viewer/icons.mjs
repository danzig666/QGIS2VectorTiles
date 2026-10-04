// Inline stroke icons of the viewer UI (24×24, currentColor). Static
// constants written for this viewer; no data ever goes into them.
const PATHS = {
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  layers: '<path d="M12 3.5l8.5 4.5-8.5 4.5L3.5 8z"/><path d="M3.5 12.5l8.5 4.5 8.5-4.5"/><path d="M3.5 16.5l8.5 4.5 8.5-4.5"/>',
  legend: '<rect x="4" y="5" width="4" height="4" rx="1"/><rect x="4" y="15" width="4" height="4" rx="1"/><path d="M11 7h9M11 17h9"/>',
  filter: '<path d="M4 5h16l-6 7.5V19l-4 1.5v-8z"/>',
  tools: '<path d="M4 17.5L17.5 4l2.5 2.5L6.5 20z"/><path d="M8 13.5l1.5 1.5M10.5 11l1.5 1.5M13 8.5l1.5 1.5"/>',
  share: '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 10.8l7.6-3.6M8.2 13.2l7.6 3.6"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 018 0v2.5"/>',
  sliders: '<path d="M5 8h8M17 8h2M5 16h2M11 16h8"/><circle cx="15" cy="8" r="2"/><circle cx="9" cy="16" r="2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 019.5 4.5a8 8 0 1010 10z"/>',
  map: '<path d="M3.5 6.5l5.5-2.5 6 2.5 5.5-2.5v13.5L15 20l-6-2.5-5.5 2.5z"/><path d="M9 4v13.5M15 6.5V20"/>',
  image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 16l5-5 4 4 2.5-2.5 5.5 5.5"/><circle cx="15.5" cy="9" r="1.5"/>',
  reset: '<path d="M4.5 12a7.5 7.5 0 102.2-5.3"/><path d="M4.5 4v4.5H9"/>',
  link: '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.5v.5"/>',
  folder: '<path d="M3.5 7a2 2 0 012-2h4l2 2h7a2 2 0 012 2v8.5a2 2 0 01-2 2h-13a2 2 0 01-2-2z"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  tag: '<path d="M3.5 12.5V4.5h8l9 9-8 8z"/><circle cx="8" cy="9" r="1.5"/>',
  print: '<path d="M7 9V4h10v5"/><rect x="4" y="9" width="16" height="7" rx="2"/><path d="M7 14h10v6H7z"/>',
  ruler: '<path d="M3.5 15.5L15.5 3.5l5 5-12 12z"/><path d="M7 12l2 2M10 9l2 2M13 6l2 2"/>',
  area: '<path d="M5 18l2-12 11 3-2 9z"/><circle cx="5" cy="18" r="1.4"/><circle cx="7" cy="6" r="1.4"/><circle cx="18" cy="9" r="1.4"/><circle cx="16" cy="18" r="1.4"/>',
  road: '<path d="M8 3L4 21M16 3l4 18M12 4v3M12 10.5v3M12 17v3"/>',
  pin: '<path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0113 0c0 4.8-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
  sparkle: '<path d="M12 3.5l1.8 5 5 1.8-5 1.8-1.8 5-1.8-5-5-1.8 5-1.8z"/>',
  person: '<circle cx="12" cy="5.5" r="2.5"/><path d="M8.5 21v-6l-1.5-1v-4a2 2 0 012-2h6a2 2 0 012 2v4l-1.5 1v6M12 14.5V21"/>',
  expand: '<path d="M4.5 9.5v-5h5M19.5 9.5v-5h-5M4.5 14.5v5h5M19.5 14.5v5h-5"/>',
};

export function icon(name, size = 20) {
  const span = document.createElement("span");
  span.className = `q2vt-icon q2vt-icon-${name}`;
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" `
    + `stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${PATHS[name] || ""}</svg>`;
  return span;
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

export function button(className, label, iconName, { title, text } = {}) {
  const node = el("button", className);
  node.type = "button";
  if (iconName) node.append(icon(iconName));
  if (text) node.append(el("span", "q2vt-btn-text", text));
  if (label) node.setAttribute("aria-label", label);
  if (title || label) node.title = title || label;
  return node;
}

// A switch (checkbox with role=switch) for layers, groups and labels.
export function toggle(label, onChange) {
  const wrap = el("label", "q2vt-switch");
  const input = el("input");
  input.type = "checkbox";
  input.setAttribute("role", "switch");
  input.setAttribute("aria-label", label);
  input.addEventListener("change", () => onChange(input.checked));
  wrap.append(input, el("span", "q2vt-switch-track"));
  return { wrap, input };
}
