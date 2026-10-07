"""
Build a self-contained comparison page from a gallery run (build_gallery.py).

For every style: QGIS and browser renders side by side, an overlay (red:
ink only QGIS draws, cyan: ink only the browser draws, grey: both agree), a
swipe slider and a blink view, each zoomable to 2x/4x with sharp pixels, so
shifts of one or two pixels are visible. Images are embedded: the page can be
opened or sent on its own.

Usage::

    python3 tools/gallery/compare_page.py /tmp/gallery --out compare.html \
        [--min 0.1] [--names "Measure,Csíkozás"] [--baseline /tmp/gallery_before]
"""

import argparse
import base64
import html
import io
import json
import os
import sys


def _load(path):
    from PIL import Image  # pylint: disable=import-outside-toplevel
    return Image.open(path).convert("RGB")


def overlay(qgis_png: str, browser_png: str):
    """Red where only QGIS inks, cyan where only the browser inks, grey where
    both agree (R = browser brightness, G = B = QGIS brightness)."""
    from PIL import Image  # pylint: disable=import-outside-toplevel
    qgis = _load(qgis_png).convert("L")
    browser = _load(browser_png).convert("L")
    if browser.size != qgis.size:
        browser = browser.resize(qgis.size)
    return Image.merge("RGB", (browser, qgis, qgis))


def _data_uri(image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _cards(gallery: str):
    images = os.path.join(gallery, "images")
    with open(os.path.join(images, "results.json"), encoding="utf-8") as handle:
        cards = json.load(handle)
    for card in cards:
        card.setdefault("views", [{"id": card["id"], "zoom": None, "score": card["score"]}])
    return images, cards


_STYLE = """
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--card:#f6f8fa;--bad:#d1242f;--good:#1a7f37;--accent:#0969da}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#3d444d;--card:#161b22;--bad:#ff7b72;--good:#3fb950;--accent:#4493f8}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;padding:16px;max-width:1400px}
h1{font-size:20px;margin:0 0 4px} p.lead{color:var(--muted);margin:0 0 12px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;margin:0 0 14px;color:var(--muted)}
.sw{display:inline-block;width:12px;height:12px;border:1px solid var(--line);vertical-align:-2px;margin-right:4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:0 0 14px}
header{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
h2{font-size:15px;margin:0} .score{font-weight:700;color:var(--bad)} .muted{color:var(--muted);font-size:12px}
.worse{color:var(--bad);font-size:12px;font-weight:600} .better{color:var(--good);font-size:12px;font-weight:600}
.tools{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.tools button{font:inherit;font-size:12px;padding:3px 9px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}
.tools button[aria-pressed=true]{border-color:var(--accent);color:var(--accent);font-weight:600}
.view{margin:6px 0 10px}
.caption{font-size:12px;color:var(--muted);margin:0 0 4px}
.scroll{overflow:auto;max-width:100%}
.grid{display:flex;gap:8px}
figure{margin:0} figcaption{font-size:12px;color:var(--muted)}
img{display:block;image-rendering:pixelated;background:#fff;border:1px solid var(--line)}
.stack{position:relative;display:inline-block;line-height:0}
.stack img.top{position:absolute;left:0;top:0}
input[type=range]{width:100%;max-width:420px}
.diag{margin:6px 0 0;padding-left:18px;font-size:12px;color:var(--muted)}
"""

_SCRIPT = """
function setMode(card, mode){
  card.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed', b.dataset.mode===mode));
  card.querySelectorAll('.mode').forEach(m=>m.hidden = m.dataset.show!==mode);
  clearInterval(card._blink);
  if(mode==='blink'){
    card._blink=setInterval(()=>card.querySelectorAll('.mode[data-show=blink] img.top').forEach(i=>{i.style.visibility=i.style.visibility==='hidden'?'visible':'hidden'}),650);
  }
}
function setZoom(card, z){
  card.querySelectorAll('[data-zoom]').forEach(b=>b.setAttribute('aria-pressed', b.dataset.zoom===String(z)));
  card.querySelectorAll('img').forEach(i=>{i.style.width=(i.naturalWidth*z)+'px'});
}
function swipe(input){
  const top=input.closest('.mode').querySelectorAll('img.top');
  top.forEach(i=>{i.style.clipPath='inset(0 0 0 '+input.value+'%)'});
}
document.querySelectorAll('.card').forEach(card=>{
  card.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(card,b.dataset.mode));
  card.querySelectorAll('[data-zoom]').forEach(b=>b.onclick=()=>setZoom(card,Number(b.dataset.zoom)));
  setMode(card,'side');
});
window.addEventListener('load',()=>document.querySelectorAll('.card').forEach(c=>setZoom(c,1)));
"""


def mismatch(score: dict) -> float:
    """The colour mismatch (build_gallery.color_score) when measured, else the
    shape mismatch of older runs."""
    return score.get("color", score.get("shape", 0.0))


def card_mismatch(card: dict) -> float:
    """A style's mismatch: its worst zoom."""
    views = card.get("views") or []
    if views and all("color" in v.get("score", {}) for v in views):
        return max(v["score"]["color"] for v in views)
    return mismatch(card["score"])


def build(gallery: str, out: str, minimum: float = 0.0, names=(), baseline: str = "",
          title: str = "QGIS vs browser comparison") -> int:
    images, cards = _cards(gallery)
    before = {}
    if baseline:
        _, old = _cards(baseline)
        before = {c["name"]: card_mismatch(c) for c in old}
    wanted = [n.lower() for n in names if n]
    chosen = [c for c in cards if card_mismatch(c) > minimum or (minimum == 0 and wanted)]
    if wanted:
        chosen = [c for c in chosen if any(w in c["name"].lower() for w in wanted)]
    chosen.sort(key=lambda c: -card_mismatch(c))
    esc = html.escape
    parts = []
    for card in chosen:
        score = card_mismatch(card)
        trend = ""
        if card["name"] in before:
            old = before[card["name"]]
            if score > old + 0.02:
                trend = f'<span class="worse">worse (was {old:.1%})</span>'
            elif score < old - 0.02:
                trend = f'<span class="better">better (was {old:.1%})</span>'
            else:
                trend = f'<span class="muted">unchanged (was {old:.1%})</span>'
        views = []
        for view in card["views"]:
            qgis = os.path.join(images, f"{view['id']}_qgis.png")
            browser = os.path.join(images, f"{view['id']}_browser.png")
            q, b, o = _data_uri(_load(qgis)), _data_uri(_load(browser)), \
                _data_uri(overlay(qgis, browser))
            label = (f"zoom {view['zoom']:g} · {mismatch(view['score']):.1%} colour mismatch"
                     if view.get("zoom") is not None and len(card["views"]) > 1 else "")
            alt = esc(card["name"])
            views.append(f"""
<div class="view">{f'<div class="caption">{esc(label)}</div>' if label else ''}
 <div class="mode scroll" data-show="side"><div class="grid">
  <figure><img src="{q}" alt="QGIS render of {alt}"><figcaption>QGIS</figcaption></figure>
  <figure><img src="{b}" alt="Browser render of {alt}"><figcaption>Browser</figcaption></figure>
  <figure><img src="{o}" alt="Overlay of {alt}"><figcaption>Overlay</figcaption></figure>
 </div></div>
 <div class="mode scroll" data-show="overlay" hidden><img src="{o}" alt="Overlay of {alt}"></div>
 <div class="mode" data-show="swipe" hidden>
  <div class="scroll"><div class="stack"><img src="{q}" alt="QGIS render of {alt}"><img class="top" src="{b}" alt="Browser render of {alt}" style="clip-path:inset(0 0 0 50%)"></div></div>
  <input type="range" min="0" max="100" value="50" oninput="swipe(this)" aria-label="Swipe between QGIS (left) and browser (right)">
 </div>
 <div class="mode scroll" data-show="blink" hidden><div class="stack"><img src="{q}" alt="QGIS render of {alt}"><img class="top" src="{b}" alt="Browser render of {alt}"></div></div>
</div>""")
        diags = "".join(f"<li><code>{esc(d['code'])}</code> {esc(d['message'])}</li>"
                        for d in card.get("diagnostics", []))
        parts.append(f"""
<section class="card"><header><h2>{esc(card['name'])}</h2><span class="{'score' if score >= 0.02 else 'muted'}">{score:.1%}</span>{trend}
<span class="muted">{esc(card.get('kind', ''))} · {esc(card.get('geometry', ''))}</span></header>
<div class="tools" role="group" aria-label="View">
 <button data-mode="side">Side by side</button><button data-mode="overlay">Overlay</button>
 <button data-mode="swipe">Swipe</button><button data-mode="blink">Blink</button>
 <span class="muted" style="align-self:center">zoom</span>
 <button data-zoom="1">1×</button><button data-zoom="2">2×</button><button data-zoom="4">4×</button>
</div>{''.join(views)}
{f'<ul class="diag">{diags}</ul>' if diags else ''}</section>""")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Render comparison</title>
<style>{_STYLE}</style></head><body>
<h1>{esc(title)}</h1>
<p class="lead">{len(chosen)} of {len(cards)} styles, worst first by colour mismatch (share of drawn pixels whose colour is not a blend of the other render's colours within 1 px, tolerance 40 levels; the worst zoom counts). Swipe: QGIS on the left of the handle, browser on the right. Blink alternates them in place.</p>
<div class="legend"><span><span class="sw" style="background:#ff0000"></span>only QGIS</span>
<span><span class="sw" style="background:#00ffff"></span>only browser</span>
<span><span class="sw" style="background:#808080"></span>both</span></div>
{''.join(parts)}<script>{_SCRIPT}</script></body></html>"""
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(page)
    return len(chosen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("gallery", help="output folder of build_gallery.py")
    parser.add_argument("--out", required=True)
    parser.add_argument("--min", type=float, default=0.0,
                        help="only styles above this mismatch (0.1 = 10 %%)")
    parser.add_argument("--names", default="", help="comma-separated name filters")
    parser.add_argument("--baseline", default="", help="an earlier gallery run to compare with")
    parser.add_argument("--title", default="QGIS vs browser comparison")
    args = parser.parse_args()
    count = build(args.gallery, args.out, args.min, args.names.split(","), args.baseline,
                  args.title)
    print(f"{args.out}: {count} styles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
