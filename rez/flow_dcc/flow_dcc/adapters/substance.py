"""Substance 3D Painter adapter.

Painter's plugin API can only add actions to the menus it already has -- there
is no API for a menu of its own -- so the Flow actions go under File,
prefixed with the menu name so they read as one group.

Painter ships PySide6 from 2024 and PySide2 before it, and QAction moved
between QtWidgets and QtGui in that jump; _qt() covers both rather than
pinning the package to one Painter release.
"""

import os
import sys

from . import MENU_NAME, actions, common

# Everything install() added, so a reload replaces the actions rather than
# adding a second copy of each.
_added = []


def _painter():
    import substance_painter
    return substance_painter


def _qt():
    """(QAction, QtWidgets) for whichever Qt this Painter ships."""
    try:
        from PySide6 import QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtGui, QtWidgets
    return getattr(QtGui, "QAction", None) or QtWidgets.QAction, QtWidgets


def install():
    painter = _painter()
    action_cls, _ = _qt()
    uninstall()

    module = sys.modules[__name__]
    for label, attr in actions(module):
        if label is None:
            continue                        # File draws its own dividers
        action = action_cls("{0}: {1}".format(MENU_NAME, label))
        action.triggered.connect(_callback(getattr(module, attr)))
        painter.ui.add_action(painter.ui.ApplicationMenu.File, action)
        _added.append(action)

    # Painter cannot make an empty project (it needs a mesh), so on a task with
    # no files common.start() logs that save_scene() refused and stops there.
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    QTimer.singleShot(0, lambda: common.start(module))
    return True


def uninstall():
    """Called by install() and by the plugin's close_plugin()."""
    painter = _painter()
    while _added:
        painter.ui.delete_ui_element(_added.pop())


def _callback(func):
    """Qt passes the action's checked state; swallow it."""
    return lambda *_: func()


def save_scene(path):
    painter = _painter()
    if not painter.project.is_open():
        raise RuntimeError("No Substance Painter project is open.")
    painter.project.save_as(path, painter.project.ProjectSaveMode.Full)
    return path


def open_scene(path):
    painter = _painter()
    if painter.project.is_open():
        if painter.project.needs_saving() and not common.discard_ok(
                sys.modules[__name__], path):
            return None
        painter.project.close()
    painter.project.open(path)
    return path


def current_scene():
    painter = _painter()
    if not painter.project.is_open():
        return ""
    return painter.project.file_path() or ""


def message(text):
    """Painter's log for the one-liner, a dialog for anything multi-line."""
    painter = _painter()
    painter.logging.info(text)
    if "\n" not in text:
        return
    _, QtWidgets = _qt()
    QtWidgets.QMessageBox.information(painter.ui.get_main_window(),
                                      "Flow", text)


def confirm(text):
    _, QtWidgets = _qt()
    answer = QtWidgets.QMessageBox.question(
        _painter().ui.get_main_window(), "Flow", text,
        QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
        QtWidgets.QMessageBox.Cancel)
    return answer == QtWidgets.QMessageBox.Ok


def ask_path(start_dir, extension, suggested=""):
    _, QtWidgets = _qt()
    start = os.path.join(start_dir, suggested) if suggested else start_dir
    path, _filter = QtWidgets.QFileDialog.getSaveFileName(
        _painter().ui.get_main_window(), "Flow: Save As", start,
        "Substance Painter Projects (*{0})".format(extension or ""))
    return path or None


# -- menu actions ---------------------------------------------------------

common.bind(sys.modules[__name__])
