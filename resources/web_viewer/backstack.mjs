// The browser's Back button closes what the visitor opened on the map (a
// feature popup, the reading window, the panel sheet on phones, Street
// View, a measuring or drawing tool), newest first, instead of leaving the
// map. Each open thing adds one history entry with the same address; Back
// lands on the entry below and closes the newest thing. Closed some other
// way (its ✕, Escape), the thing takes its entry back with history.back().
//
// The viewer keeps the address current with replaceUrl (the permalink
// hash), so a Back that lands on an older entry gets today's address again
// and is not taken for a pasted link (see isOwnTraversal).

const MARK = "q2vtBack";
const stack = [];  // [{ close }] newest last, one history entry each
let skips = 0;     // history.back() calls of our own still to arrive
let latest = null; // the address the viewer last set
let ownUntil = 0;  // hashchange events before this time are our traversals

function restoreUrl() {
  if (latest && location.href !== latest) {
    history.replaceState(history.state, "", latest);
    ownUntil = performance.now() + 1000;
  }
}

window.addEventListener("popstate", (event) => {
  ownUntil = performance.now() + 1000;
  if (skips > 0) {
    skips -= 1;
    restoreUrl();
    return;
  }
  const top = stack.pop();
  if (top) {
    restoreUrl();
    try { top.close(); } catch (error) { console.warn("back:", error); }  // eslint-disable-line no-console
    return;
  }
  // An entry left from before a reload: nothing open belongs to it.
  if (event.state && event.state[MARK]) {
    skips += 1;
    history.back();
    return;
  }
  ownUntil = 0;  // a real navigation inside the page: the hash handler decides
});

// The viewer's own address updates (keeps this entry's state).
export function replaceUrl(url) {
  history.replaceState(history.state, "", url);
  latest = location.href;
}

// Whether a hashchange comes from a Back that only closed something.
export function isOwnTraversal() {
  return performance.now() < ownUntil;
}

// Something just opened: Back calls ``close``. Returns release(), to call
// when it closes in any other way (calling it again does nothing).
export function onBack(close) {
  const entry = { close };
  latest = latest || location.href;
  history.pushState({ [MARK]: true }, "", location.href);
  stack.push(entry);
  return () => {
    const index = stack.indexOf(entry);
    if (index < 0) return;  // closed by Back: its entry is gone already
    stack.splice(index, 1);
    skips += 1;
    history.back();
  };
}

// Open/close bookkeeping for one closable thing: open() registers it once,
// closed() releases it. A close followed by an open in the same task (a
// tool restarted in another mode) keeps the one entry.
export class BackEntry {
  constructor(close) { this.close = close; this.release = null; this.pending = null; }

  open() {
    if (this.pending) { clearTimeout(this.pending); this.pending = null; }
    if (this.release) return;
    this.release = onBack(() => {
      if (this.pending) { clearTimeout(this.pending); this.pending = null; }
      this.release = null;
      this.close();
    });
  }

  closed() {
    if (!this.release || this.pending) return;
    this.pending = setTimeout(() => {
      this.pending = null;
      const release = this.release;
      this.release = null;
      if (release) release();
    }, 0);
  }
}

export function depth() { return stack.length; }
