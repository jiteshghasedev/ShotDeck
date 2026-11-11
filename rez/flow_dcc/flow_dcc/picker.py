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


def ask_start(departments, current, files_for, title="Flow: Open Script"):
    """Department and file dropdowns, with Open and New.

    files_for(department) gives that department folder's task_files().
    Returns ("open", department, path), ("new", department, None), or None
    on cancel. Raises ImportError when the host has no Qt.
    """
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets

    dialog = QtWidgets.QDialog(QtWidgets.QApplication.activeWindow())
    dialog.setWindowTitle(title)
    dialog.resize(560, 140)
    form = QtWidgets.QFormLayout(dialog)
    dept_box = QtWidgets.QComboBox()
    dept_box.addItems(departments)
    file_box = QtWidgets.QComboBox()
    form.addRow("Department", dept_box)
    form.addRow("File", file_box)

    buttons = QtWidgets.QDialogButtonBox()
    open_button = buttons.addButton("Open", QtWidgets.QDialogButtonBox.AcceptRole)
    new_button = buttons.addButton("New", QtWidgets.QDialogButtonBox.ActionRole)
    buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
    form.addRow(buttons)

    shown = []
    choice = []

    def refill(_=None):
        shown[:] = files_for(dept_box.currentText())
        file_box.clear()
        file_box.addItems([r[4] + "   " + label(r) for r in rows(shown)])
        open_button.setEnabled(bool(shown))

    def pick(action):
        path = shown[file_box.currentIndex()]["path"] \
            if action == "open" and shown else None
        choice[:] = [(action, dept_box.currentText(), path)]
        dialog.accept()

    dept_box.currentIndexChanged.connect(refill)
    open_button.clicked.connect(lambda: pick("open"))
    new_button.clicked.connect(lambda: pick("new"))
    buttons.rejected.connect(dialog.reject)
    dept_box.setCurrentIndex(max(0, dept_box.findText(current)))
    refill()

    run = getattr(dialog, "exec", None) or dialog.exec_
    run()
    return choice[0] if choice else None
