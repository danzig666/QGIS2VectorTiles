"""Publish window, Interaction tab: the Viewer checkboxes fill the width in
equal columns (a wide window gets more columns and fewer rows), never overlap
or get cut off, the tab can still be made narrow (down to one column), and
its minimum height follows the rows at the current width."""

import os

from qgis.PyQt.QtWidgets import QApplication

from q2vt_fixtures import reset_project


def _rows_and_columns(dialog):
    boxes = list(dialog.i_flags.values())
    rects = [box.geometry() for box in boxes]
    for index, rect in enumerate(rects):
        assert rect.width() >= boxes[index].sizeHint().width(), "a checkbox is cut off"
        for other in rects[index + 1:]:
            assert not rect.intersects(other), "checkboxes overlap"
    key = dialog.i_google_key.geometry()
    assert not any(key.intersects(rect) for rect in rects)
    assert key.right() < dialog.i_flags_box.width()
    assert max(rect.bottom() for rect in rects + [key]) < dialog.i_flags_box.height()
    return len({rect.top() for rect in rects}), len({rect.left() for rect in rects})


def _show(dialog, tab, width):
    tab.resize(width, 1200)
    for _ in range(6):  # the layout settles after the row count changes
        QApplication.processEvents()
    shots = os.environ.get("Q2VT_SCREENSHOTS")
    if shots:
        tab.grab().save(os.path.join(shots, f"viewer_options_{width}.png"))
    return _rows_and_columns(dialog)


def test_viewer_options_reflow_with_the_window_width(plugin):
    reset_project()
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    dialog = PublishDialog(iface=None)
    tab = dialog.tabs.widget(1)
    tab.setParent(None)  # the Interaction tab alone (the Map tab sets the window's minimum width)
    tab.show()
    try:
        wide_rows, wide_columns = _show(dialog, tab, 1900)
        wide_height = tab.minimumSizeHint().height()
        rows, columns = _show(dialog, tab, 1300)
        # As narrow as the rest of the tab allows: the options never set
        # the minimum width (one column would do).
        narrowest = tab.minimumSizeHint().width()
        narrow_rows, narrow_columns = _show(dialog, tab, narrowest)
        print("LAYOUT", (wide_rows, wide_columns), (rows, columns), (narrow_rows, narrow_columns), narrowest)
        assert wide_columns > 3 and wide_rows < 6, (wide_rows, wide_columns)  # was always 3 x 6
        assert wide_columns > columns >= narrow_columns and wide_rows < rows <= narrow_rows
        assert dialog.i_flags_box.minimumSizeHint().width() < 500
        _show(dialog, tab, 1900)
        assert tab.minimumSizeHint().height() == wide_height  # not the one-column height
    finally:
        tab.close()
        dialog.close()
