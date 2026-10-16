"""
Finding layers by name in long lists (projects with hundreds of layers):
text matching that ignores case and accents, and layer drop-downs that
take typing and offer the layers whose name contains the text.
"""

import unicodedata

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import QComboBox, QCompleter


def tr(text: str) -> str:
    return QCoreApplication.translate("LayerSearch", text)


def fold(text: str) -> str:
    """Text for matching: lower case, accents removed ("Övezet" ~ "ovezet")."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def matches(text: str, query: str) -> bool:
    """Does ``text`` contain ``query`` (case and accents ignored)?"""
    return fold(query) in fold(text)


def searchable(combo: QComboBox) -> QComboBox:
    """Let a layer drop-down take typing: a list of the layers whose name
    contains the typed text (upper or lower case) opens under it, and
    choosing one selects it. Text that names no layer is undone when the
    box loses focus, so the selection is always a real entry."""
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    completer = QCompleter(combo.model(), combo)
    completer.setCompletionColumn(combo.modelColumn())
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    completer.setMaxVisibleItems(15)
    combo.setCompleter(completer)
    if combo.lineEdit() is not None:
        combo.lineEdit().setPlaceholderText(tr("Type to find a layer…"))

    def chosen(text):
        index = combo.findText(text, Qt.MatchFlag.MatchExactly)
        if index >= 0:
            combo.setCurrentIndex(index)

    def finished():
        text = combo.currentText()
        if text == combo.itemText(combo.currentIndex()):
            return  # the chosen layer (two layers may share its name: findText finds the first)
        index = combo.findText(text, Qt.MatchFlag.MatchExactly)
        if index >= 0:
            if index != combo.currentIndex():
                combo.setCurrentIndex(index)
        else:  # no such layer: back to the chosen one
            combo.setEditText(combo.itemText(combo.currentIndex()))

    completer.activated[str].connect(chosen)
    combo.lineEdit().editingFinished.connect(finished)
    return combo
