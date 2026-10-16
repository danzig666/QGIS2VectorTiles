// The browser's Back button closes what the visitor opened on the map (a
// feature popup, the reading window, the panel sheet on phones, Street
// View, a measuring or drawing tool), newest first, instead of leaving the
// map. Each open thing adds one history entry with the same address; Back
// lands on the entry below and closes the newest thing. Closed some other
// way (its ✕, Escape), the thing takes its entry back with history.back().
//
// Each entry records how many things were open there (its depth), so Back
// closes what is above it, Forward onto a closed thing's entry is undone, and
// a pasted link (an entry without a depth) is left to the hash handler. The
// viewer keeps the address current with replaceUrl (the permalink hash), so a
// Back that lands on an older entry gets today's address again and is not
// taken for a pasted link (see isOwnTraversal).

const MARK = "q2vtBack";  // history state: how many things were open at this entry
const stack = [];  // [{ close }] newest last, one history entry each
let latest = null; // the address the viewer last set
let ownUntil = 0;  // hashchange events before this time are our traversals

const depthOf = (state) => (state && Number.isInteger(state[MARK]) ? state[MARK] : null);

function restoreUrl() {
  if (latest && location.href !== latest) {
    history.replaceState(history.state, "", latest);
    ownUntil = performance.now() + 1000;
  }
}

// (Only in a browser: the pure helpers that import this also run in Node.)
globalThis.window?.addEventListener("popstate", (event) => {
  const depth = depthOf(event.state);
  if (depth === null) {
    // An entry the viewer did not make (a link pasted into the address
    // bar): the hash handler opens it.
    ownUntil = 0;
    return;
  }
  ownUntil = performance.now() + 1000;
  if (depth > stack.length) {
    // Forward onto the entry of something already closed (or an entry left
    // from before a reload): go back to where the open things are.
    history.go(stack.length - depth);
    return;
  }
  restoreUrl();
  // Back (one step or several): close what was opened after this entry.
  while (stack.length > depth) {
    const top = stack.pop();
    try { top.close(); } catch (error) { console.warn("back:", error); }  // eslint-disable-line no-console
  }
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
  if (!stack.length && depthOf(history.state) !== 0) {
    history.replaceState({ ...(history.state || {}), [MARK]: 0 }, "", location.href);
  }
  stack.push(entry);
  history.pushState({ [MARK]: stack.length }, "", location.href);
  return () => {
    const index = stack.indexOf(entry);
    if (index < 0) return;  // closed by Back: its entry is gone already
    stack.splice(index, 1);
    // One entry fewer: the Back lands on depth stack.length, nothing to close.
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
