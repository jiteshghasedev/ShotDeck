"""The Load File window: one row per work file on the task.

Qt, because every host with a Python UI but Blender ships PySide2 or PySide6.
A host without Qt (Blender, Rhino) gives its adapter a pick_file() of its own;
common.load_file() prefers that over ask() here. No host module is imported,
so the rows can be tested outside a DCC.
"""

import datetime

from . import lock

COLUMNS = ("Version", "Artist", "Saved", "Open by", "File")


def rows(files):
    """Display text for paths.task_files() entries, in the same order."""
    out = []
    for f in files:
        saved = datetime.datetime.fromtimestamp(f["mtime"])
        held = lock.holder(f["path"])
        out.append(("v{0:04d}".format(f["version"]), f["user"] or "-",
                    saved.strftime("%Y-%m-%d %H:%M"),
                    lock.describe(held) if held else "", f["name"]))
    return out


def label(row):
    """One line per file, for hosts that can only show a list of strings."""
    text = "{0}   {1}   {2}".format(*row[:3])
    return text + ("   [open by {0}]".format(row[3]) if row[3] else "")


def ask(files, title="Flow: Load File"):
    """Show the window and return the chosen path, or None on cancel.

    Raises ImportError when the host has no Qt, so the caller can say so
    rather than show nothing.
    """
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets

    parent = QtWidgets.QApplication.activeWindow()
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(720, 360)
    layout = QtWidgets.QVBoxLayout(dialog)

    tree = QtWidgets.QTreeWidget()
    tree.setHeaderLabels(list(COLUMNS))
    tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(True)
    for row in rows(files):
        tree.addTopLevelItem(QtWidgets.QTreeWidgetItem(list(row)))
    for column in range(len(COLUMNS)):
        tree.resizeColumnToContents(column)
    tree.setCurrentItem(tree.topLevelItem(0))
    tree.itemDoubleClicked.connect(lambda *_: dialog.accept())
    layout.addWidget(tree)

    buttons = QtWidgets.QDialogButtonBox()
    buttons.addButton("Open", QtWidgets.QDialogButtonBox.AcceptRole)
    buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    run = getattr(dialog, "exec", None) or dialog.exec_
    if not run():
        return None
    index = tree.indexOfTopLevelItem(tree.currentItem())
    return files[index]["path"] if index >= 0 else None
