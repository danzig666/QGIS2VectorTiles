"""
Simple HTML of long texts published with the map (e.g. the full zone
regulations of a zoning plan): headings, paragraphs, lists, emphasis and
plain tables, nothing else. Everything is rebuilt from the parsed text:
other tags are dropped (their text kept; scripts, styles and embedded
content dropped whole), every attribute is dropped except a table cell's
numeric colspan/rowspan, and the text is escaped. The viewer cleans it
again the same way (rich_text.mjs) before showing it.
"""

import html
import re
from html.parser import HTMLParser

ALLOWED = {"h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "strong", "em", "b", "i", "u", "br", "sup",
           "sub", "table", "thead", "tbody", "tr", "th", "td", "blockquote", "hr"}
RENAMED = {"h1": "h3", "h2": "h3"}       # the panel's own heading is above
EMPTY = {"br", "hr"}
DROPPED = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math",
           "head", "title", "textarea", "select", "button", "form"}
SPANS = {"colspan", "rowspan"}


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.open, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        tag = RENAMED.get(tag, tag)
        if tag in DROPPED:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        spans = "".join(f' {name}="{int(value)}"' for name, value in attrs
                        if tag in ("td", "th") and name in SPANS and value and value.isdigit()
                        and 1 <= int(value) <= 50)
        if tag in EMPTY:
            self.out.append(f"<{tag}>")
            return
        # A new item, paragraph, row or cell ends the open one (as browsers do).
        siblings = {"li": ("li",), "p": ("p",), "tr": ("tr", "td", "th"), "td": ("td", "th"), "th": ("td", "th")}
        while self.open and self.open[-1] in siblings.get(tag, ()):
            self.out.append(f"</{self.open.pop()}>")
        self.out.append(f"<{tag}{spans}>")
        self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if RENAMED.get(tag, tag) in EMPTY and not self.skip:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        tag = RENAMED.get(tag, tag)
        if tag in DROPPED:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in self.open:
            return
        while self.open:  # close what was left open inside it
            last = self.open.pop()
            self.out.append(f"</{last}>")
            if last == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def clean_html(text) -> str:
    """The text as simple, safe HTML (see the module docstring)."""
    cleaner = _Cleaner()
    cleaner.feed(str(text or ""))
    cleaner.close()
    cleaner.out.extend(f"</{tag}>" for tag in reversed(cleaner.open))
    out = "".join(cleaner.out)
    return re.sub(r"[ \t\r\f\v]*\n[\s]*", "\n", out).strip()
