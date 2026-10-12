// Stable entry point of a publication: reads current.json (never from a
// stale cache) and opens the exact release it names, keeping the query and
// hash (camera / layer / feature state of a shared link). The release page
// then shows this address again (sessionStorage "q2vt:entry"): visitors
// see and share the short, stable address, not the release's own.
const RELEASE = /^r-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$/;
const status = document.getElementById("q2vt-status");

function fail(message) {
  status.textContent = message;
  status.setAttribute("role", "alert");
}

try {
  const base = new URL(".", location.href);
  const response = await fetch(new URL("current.json", base), { cache: "no-store" });
  if (!response.ok) throw new Error(`current.json: HTTP ${response.status}`);
  const pointer = await response.json();
  const id = pointer && pointer.releaseId;
  if (pointer.schemaVersion !== 1 || !RELEASE.test(id || "") ||
      pointer.entry !== `releases/${id}/index.html`) {
    throw new Error("current.json is invalid");
  }
  const target = new URL(pointer.entry, base);
  if (target.origin !== base.origin || !target.pathname.startsWith(base.pathname)) {
    throw new Error("current.json points outside this publication");
  }
  target.search = location.search;
  target.hash = location.hash;
  try {
    sessionStorage.setItem("q2vt:entry", JSON.stringify({ entry: location.pathname, release: target.pathname }));
  } catch { /* storage unavailable: the release address stays */ }
  location.replace(target.href);
} catch (error) {
  fail(`The map could not be opened: ${error.message}`);
}
