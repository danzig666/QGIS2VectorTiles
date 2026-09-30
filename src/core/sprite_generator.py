"""
sprite_generator.py

Generates MapLibre-compatible sprite sheets from QGIS marker symbols and
pre-rendered pattern images. Output: sprite.png / sprite.json and
sprite@2x.png / sprite@2x.json.

Rendering and packing are separate steps:

* ``SymbolImage`` renders one *whole QGIS symbol* (never a bare symbol layer)
  at a given scale. Rendering errors propagate as exceptions — they are never
  replaced by a transparent image — and a symbol that is transparent *by
  design* is reported as such.
* ``fidelity.assets.pack`` lays completed images out deterministically.
* The @2x sheet is rendered at twice the resolution, not upscaled from @1x.

scale_factor oversamples symbols (icons only): the image is rendered
scale_factor× larger and declares ``pixelRatio = scale_factor`` so its
logical size — what MapLibre lays out — is the symbol's real size.
"""

import math
from io import BytesIO
from dataclasses import dataclass, field
from json import dumps
from os import makedirs
from os.path import join
from typing import Dict, List, Optional, TypeAlias, Union


from PIL import Image
from qgis.core import (Qgis, QgsExpressionContext, QgsExpressionContextScope,
                       QgsExpressionContextUtils, QgsFeature, QgsFields, QgsField,
                       QgsMapToPixel, QgsMarkerSymbol, QgsRenderContext, QgsSymbol)
from qgis.PyQt.QtCore import qVersion

from .fidelity.assets import AtlasEntry, pack, symmetric_crop_box
from .fidelity.diagnostics import DiagnosticCollector

_QT_VERSION = int(qVersion()[0])
if _QT_VERSION == 5:
    from PyQt5.QtCore import QSize, QBuffer, QIODevice, QPointF
    from PyQt5.QtGui import QImage, QPainter
    _IO_READ_WRITE = QIODevice.ReadWrite
    _ARGB = QImage.Format_ARGB32_Premultiplied
    _ANTIALIAS = QPainter.Antialiasing
else:
    from PyQt6.QtCore import QSize, QBuffer, QIODevice, QIODeviceBase as QIODevice, QPointF
    from PyQt6.QtGui import QImage, QPainter
    _IO_READ_WRITE = QIODevice.OpenModeFlag.ReadWrite
    _ARGB = QImage.Format.Format_ARGB32_Premultiplied
    _ANTIALIAS = QPainter.RenderHint.Antialiasing

Img: TypeAlias = Image.Image

_BASE_CANVAS_PX = 1000
_MAX_CANVAS_PX = 4096
_PX_PER_MM = 96.0 / 25.4


class SpriteInputError(TypeError):
    """A renderer received something other than a whole ``QgsSymbol``."""


class SpriteRenderError(RuntimeError):
    """QGIS failed to render a symbol."""


@dataclass
class SpriteRequest:
    """A symbol to render into the sprite sheet.

    ``bake_rotation``: keep the symbol's own angle in the image. Point
    markers set it to False because the style applies ``icon-rotate``;
    rotating in both places would double the rotation.
    """

    symbol: QgsSymbol
    bake_rotation: bool = True
    # Map units per logical sprite pixel for symbols sized in map units; the
    # style scales the icon with zoom (see maplibre_converter).
    map_units_per_pixel: float = 1.0
    # Attribute values used to evaluate data-defined properties (variants).
    attributes: Optional[Dict[str, object]] = None
    # Oversampling (= declared pixelRatio of the 1x image); None: generator default.
    oversampling: Optional[float] = None


@dataclass
class PatternImages:
    """Pre-rendered periodic pattern cell at 1x and 2x (no cropping)."""

    img_1x: Img
    img_2x: Img
    # Stretch metadata (logical px), e.g. for label background frames.
    metadata: Optional[Dict[str, object]] = None


@dataclass
class SymbolImage:
    """Render a QGIS symbol as a centre-preserving cropped PIL image."""

    symbol: QgsSymbol
    name: str
    scale_factor: float = 1
    bake_rotation: bool = True
    map_units_per_pixel: float = 1.0
    attributes: Optional[Dict[str, object]] = None
    img: Img = field(init=False)
    width: int = field(init=False)
    height: int = field(init=False)
    transparent: bool = field(init=False, default=False)

    def __post_init__(self):
        if not isinstance(self.symbol, QgsSymbol):
            raise SpriteInputError(
                f"Sprite '{self.name}' expects a QgsSymbol, got {type(self.symbol).__name__}; "
                "wrap symbol layers in a matching symbol first."
            )
        self._render()

    def _extract_markers(self, symbol: QgsSymbol, markers: list):
        """Recursively extract marker symbols from a symbol object"""
        if symbol.type() == QgsSymbol.SymbolType.Marker:
            markers.append(symbol)
        else:
            for sym_layer in symbol.symbolLayers():
                sub_symbol = sym_layer.subSymbol()
                if sub_symbol:
                    self._extract_markers(sub_symbol, markers)

    def _set_symbol_size(self, marker, scale_factor):
        """Set marker symbol size including outlines and buffer width"""
        if hasattr(marker, "size") and hasattr(marker, "setSize"):
            marker.setSize(marker.size() * scale_factor)
        for sym_layer in marker.symbolLayers():
            if hasattr(sym_layer, 'strokeWidth') and hasattr(sym_layer, 'setStrokeWidth'):
                sym_layer.setStrokeWidth(sym_layer.strokeWidth() * scale_factor)
        if hasattr(marker, "bufferSettings") and marker.bufferSettings():
            marker.bufferSettings().setSize(marker.bufferSettings().size() * scale_factor)

    def _marker_canvas(self, symbol: QgsMarkerSymbol, context_factory, feature=None,
                       expression_context=None) -> int:
        """Canvas size (px) that holds the marker drawn at the image centre
        (with the variant's attributes: e.g. a data-defined character)."""
        probe = QImage(1, 1, _ARGB)
        painter = QPainter(probe)
        try:
            context = context_factory(painter)
            if expression_context is not None:
                context.setExpressionContext(QgsExpressionContext(expression_context))
            symbol.startRender(context, feature.fields() if feature else QgsFields())
            bounds = symbol.bounds(QPointF(0, 0), context, feature or QgsFeature())
            symbol.stopRender(context)
        finally:
            painter.end()
        extent = max(abs(bounds.left()), abs(bounds.right()), abs(bounds.top()),
                     abs(bounds.bottom()), 1.0)
        canvas = int(math.ceil(2 * extent)) + 16
        if canvas > _MAX_CANVAS_PX:
            raise SpriteRenderError(
                f"Sprite '{self.name}' would be {canvas} px wide; reduce its size or "
                "the export's zoom range")
        return canvas

    def _context(self, painter):
        context = QgsRenderContext.fromQPainter(painter)
        context.setScaleFactor(_PX_PER_MM * self.scale_factor)
        context.setMapToPixel(QgsMapToPixel(self.map_units_per_pixel / self.scale_factor))
        context.setFlag(Qgis.RenderContextFlag.Antialiasing, True)
        return context

    def _render_marker(self, symbol: QgsMarkerSymbol, canvas: int = 0) -> QImage:
        """Draw a marker at the image centre with an explicit render context.

        Physical units use ``scale_factor`` × 96 DPI; map units use
        ``map_units_per_pixel`` / ``scale_factor``. (``asImage`` previews
        render map units at an arbitrary fixed scale.) The canvas is sized
        from the symbol's rendered bounds.
        """
        expression_context = QgsExpressionContext([QgsExpressionContextUtils.globalScope()])
        feature = None
        if self.attributes:
            fields = QgsFields()
            for name in self.attributes:
                fields.append(QgsField(name))
            feature = QgsFeature(fields)
            for name, value in self.attributes.items():
                feature.setAttribute(name, value)
            scope = QgsExpressionContextScope()
            scope.setFeature(feature)
            scope.setFields(fields)
            expression_context.appendScope(scope)
        canvas = self._marker_canvas(symbol, self._context, feature, expression_context)
        image = QImage(canvas, canvas, _ARGB)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(_ANTIALIAS)
        context = self._context(painter)
        context.setExpressionContext(expression_context)
        try:
            symbol.startRender(context, feature.fields() if feature else QgsFields())
            symbol.renderPoint(QPointF(canvas / 2.0, canvas / 2.0), feature, context)
            symbol.stopRender(context)
        finally:
            painter.end()
        return image

    def _render(self):
        """Render at the requested scale and crop symmetrically about the origin."""
        symbol = self.symbol.clone()
        if not self.bake_rotation and isinstance(symbol, QgsMarkerSymbol):
            symbol.setAngle(0)
        canvas = int(_BASE_CANVAS_PX * max(1.0, self.scale_factor / 3.0))
        if isinstance(symbol, QgsMarkerSymbol):
            qt_img = self._render_marker(symbol)
        else:
            # Fill/line previews (pattern approximations): legacy size scaling.
            markers = []
            self._extract_markers(symbol, markers)
            for marker in markers:
                self._set_symbol_size(marker, self.scale_factor)
            qt_img = symbol.asImage(QSize(canvas, canvas))
        if qt_img is None or qt_img.isNull():
            raise SpriteRenderError(f"QGIS returned an empty image for sprite '{self.name}'")
        pil_img = self._qt_to_pil(qt_img)
        bbox = pil_img.getbbox()
        if bbox is None:
            # Transparent by design (e.g. fully transparent colors): keep a
            # valid 1x1 image and flag it, distinct from a failed render.
            self.transparent = True
            self.img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        else:
            self.img = pil_img.crop(symmetric_crop_box(bbox, pil_img.width, pil_img.height))
        self.img.name = self.name
        self.width = self.img.width
        self.height = self.img.height

    @staticmethod
    def _qt_to_pil(qt_img: QImage) -> Img:
        """Convert a QImage to an RGBA PIL image (errors propagate)."""
        buffer = QBuffer()
        buffer.open(_IO_READ_WRITE)
        if not qt_img.save(buffer, "PNG"):
            raise SpriteRenderError("Could not encode rendered symbol as PNG")
        bio = BytesIO(bytes(buffer.data()))
        buffer.close()
        bio.seek(0)
        img = Image.open(bio)
        img.load()
        return img.convert("RGBA")


class SpriteGenerator:
    """Orchestrate the sprite pipeline: render → pack → JSON → save."""

    def __init__(
        self,
        symbols_dict: Dict[str, Union[QgsSymbol, SpriteRequest]],
        output_dir: str,
        scale_factor: int = 1,
        test_mode: bool = False,
        diagnostics: Optional[DiagnosticCollector] = None,
        pattern_images: Optional[Dict[str, PatternImages]] = None,
    ):
        self.symbols_dict = symbols_dict
        self.pattern_images = pattern_images or {}
        self.output_dir = output_dir
        self.scale_factor = scale_factor
        self.lower_factor = 2
        self.test_mode = test_mode
        self.diagnostics = diagnostics or DiagnosticCollector()
        self.failed: Dict[str, str] = {}
        self.names: List[str] = []
        self.index: Dict[int, Dict[str, dict]] = {}

    def _render_entry(self, name: str, request) -> Optional[AtlasEntry]:
        if not isinstance(request, SpriteRequest):
            request = SpriteRequest(request)
        try:
            quality = request.oversampling or self.scale_factor
            one = SymbolImage(request.symbol, name, quality, request.bake_rotation,
                              request.map_units_per_pixel, request.attributes)
            two = SymbolImage(request.symbol, name, quality * self.lower_factor,
                              request.bake_rotation, request.map_units_per_pixel,
                              request.attributes)
        except SpriteInputError as err:
            self.failed[name] = str(err)
            self.diagnostics.add("Q2VT_SPRITE_WRONG_INPUT", str(err), component=name)
            return None
        except Exception as err:  # noqa: BLE001 - any QGIS/PIL failure is reported
            self.failed[name] = str(err)
            self.diagnostics.add("Q2VT_SPRITE_RENDER_FAILED",
                                 f"Sprite '{name}' could not be rendered: {err}",
                                 component=name, detail=repr(err))
            return None
        if one.transparent:
            self.diagnostics.add("Q2VT_SPRITE_TRANSPARENT",
                                 f"Sprite '{name}' is fully transparent", component=name)
        # Oversampled images declare their oversampling as pixelRatio so the
        # logical icon size equals the symbol size (icon-size 1).
        return AtlasEntry(name, {
            1: (one.img, round(quality, 4)),
            self.lower_factor: (two.img, round(quality * self.lower_factor, 4)),
        })

    def generate(self) -> Optional[str]:
        """Run the sprite pipeline; return the output directory or None if nothing was written."""
        entries: List[AtlasEntry] = []
        for name, request in self.symbols_dict.items():
            entry = self._render_entry(name, request)
            if entry:
                entries.append(entry)
        for name, images in self.pattern_images.items():
            entries.append(AtlasEntry(name, {1: (images.img_1x, 1),
                                             self.lower_factor: (images.img_2x, self.lower_factor)},
                                      dict(images.metadata or {})))
        if not entries:
            return None
        atlas = pack(entries, ratios=(1, self.lower_factor))
        self.index = atlas.index
        self.names = sorted(atlas.index[1])

        sprite_dir = join(self.output_dir, "sprite")
        makedirs(sprite_dir, exist_ok=True)
        atlas.sheets[1].save(join(sprite_dir, "sprite.png"))
        atlas.sheets[self.lower_factor].save(join(sprite_dir, f"sprite@{self.lower_factor}x.png"))
        with open(join(sprite_dir, "sprite.json"), "w", encoding="utf8") as f:
            f.write(dumps(atlas.index[1], indent=2))
        with open(join(sprite_dir, f"sprite@{self.lower_factor}x.json"), "w", encoding="utf8") as f:
            f.write(dumps(atlas.index[self.lower_factor], indent=2))

        if self.test_mode:
            self._verify_coordinates(atlas)
        return self.output_dir

    def _verify_coordinates(self, atlas):
        """Extract and save individual crops to verify sprite coordinates."""
        for ratio, sprite in atlas.sheets.items():
            test_dir = join(self.output_dir, f"test_{ratio}x")
            makedirs(test_dir, exist_ok=True)
            for name, c in atlas.index[ratio].items():
                x, y, w, h = c["x"], c["y"], c["width"], c["height"]
                crop = sprite.crop((x, y, x + w, y + h))
                safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
                crop.save(join(test_dir, f"{safe_name}.png"))


if __name__ == "__console__":
    pass
