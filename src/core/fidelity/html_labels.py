"""
html_labels.py

QGIS labels with "Allow HTML formatting" as MapLibre ``format`` sections.

QGIS renders a subset of HTML in label text (``QgsTextDocument``). MapLibre
has no markup, but its ``format`` expression draws consecutive text sections
at their own scale. The exporter stores each label's sections as fields
(text + font scale), and the style lists them:

    ["format", ["get", "q2vt_label_h0"], {"font-scale": ["get", "q2vt_label_s0"]}, ...]

Handled: ``<sub>``/``<sup>`` (drawn at 2/3 size, as QGIS does), ``<small>``
(Qt's next smaller size), ``<big>``, ``<br>`` (line break) and character
entities; other tags are dropped and keep their text. A closing tag ends the
innermost size change, as Qt does for mismatched tags (``<sub>..</small>``).
"""

import html
import re
from typing import List, Tuple

MAX_SECTIONS = 6
TEXT_FIELD = "q2vt_label_h{}"
SCALE_FIELD = "q2vt_label_s{}"

# QgsTextRenderer::SUPERSCRIPT_SUBSCRIPT_FONT_SIZE_SCALING_FACTOR
SUB_SUP_SCALE = 2.0 / 3.0
# Qt HTML sizes: <small> one step below the default (10 pt vs 12 pt).
SMALL_SCALE = 10.0 / 12.0
BIG_SCALE = 14.0 / 12.0
_SCALES = {"sub": SUB_SUP_SCALE, "sup": SUB_SUP_SCALE, "small": SMALL_SCALE, "big": BIG_SCALE}
_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z0-9]+)[^>]*?(/?)\s*>")


def html_sections(text) -> List[Tuple[str, float]]:
    """``[(text, scale)]`` of an HTML label, consecutive equal scales merged."""
    if text is None:
        return []
    text = str(text)
    sections: List[Tuple[str, float]] = []
    stack: List[float] = []

    def add(chunk):
        if not chunk:
            return
        chunk = html.unescape(chunk)
        scale = 1.0
        for factor in stack:
            scale *= factor
        scale = round(scale, 4)
        if sections and sections[-1][1] == scale:
            sections[-1] = (sections[-1][0] + chunk, scale)
        else:
            sections.append((chunk, scale))

    position = 0
    for match in _TAG.finditer(text):
        add(text[position:match.start()])
        position = match.end()
        closing, name = match.group(1), match.group(2).lower()
        if name == "br":
            add("\n")
        elif name in _SCALES:
            if closing:
                if stack:
                    stack.pop()
            else:
                stack.append(_SCALES[name])
        elif closing and name in ("p", "div") and sections:
            add("\n")
    add(text[position:])
    if len(sections) > MAX_SECTIONS:  # the rest keeps the last section's scale
        head, tail = sections[:MAX_SECTIONS - 1], sections[MAX_SECTIONS - 1:]
        sections = head + [("".join(t for t, _ in tail), tail[0][1])]
    return sections


def section_text(text, index: int) -> str:
    sections = html_sections(text)
    return sections[index][0] if index < len(sections) else ""


def section_scale(text, index: int) -> float:
    sections = html_sections(text)
    return sections[index][1] if index < len(sections) else 1.0


def format_expression(scale_is_field: bool = True) -> list:
    """MapLibre ``text-field`` drawing the stored sections."""
    expression: list = ["format"]
    for index in range(MAX_SECTIONS):
        expression += [["coalesce", ["get", TEXT_FIELD.format(index)], ""],
                       {"font-scale": ["to-number", ["get", SCALE_FIELD.format(index)], 1]}]
    return expression


def register_expression_functions() -> None:
    """``q2vt_html_text(text, i)`` and ``q2vt_html_scale(text, i)`` for the
    exporter's field calculation (idempotent)."""
    from qgis.core import QgsExpression  # pylint: disable=import-outside-toplevel
    from qgis.core import qgsfunction  # pylint: disable=import-outside-toplevel

    if QgsExpression.isFunctionName("q2vt_html_text"):
        return

    # The legacy ``args=2`` form crashes QGIS 3.34; arguments come evaluated.
    @qgsfunction(group="Custom", referenced_columns=[])
    def q2vt_html_text(text, index):
        return section_text(text, int(index))

    @qgsfunction(group="Custom", referenced_columns=[])
    def q2vt_html_scale(text, index):
        return section_scale(text, int(index))

    # QGIS keeps only a pointer: without a reference the functions are
    # garbage collected and the next evaluation crashes.
    _REGISTERED.extend([q2vt_html_text, q2vt_html_scale])


_REGISTERED: list = []
