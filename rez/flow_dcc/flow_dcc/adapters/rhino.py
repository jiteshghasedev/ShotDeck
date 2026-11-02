"""Rhino adapter.

Rhino's menus live in a .rui workspace file that is edited in the UI and
loaded per user; there is no supported way to add one from Python, and writing
into someone's .rui at startup is not a trade worth making for a menu. So
install() reports rather than pretends, and the actions are run from the
Python editor or bound to aliases -- see startup/rhino/README.txt.

Everything below the menu is the same pipeline as every other host: the same
names, the same versions, the same publish.
"""

import sys

from . import MENU_NAME, common


def _rhino():
    """RhinoCommon, which owns the document. Rhino-only import."""
    import Rhino
    return Rhino


def _rs():
    import rhinoscriptsyntax as rs
    return rs


def _doc():
    doc = _rhino().RhinoDoc.ActiveDoc
    if doc is None:
        raise RuntimeError("No open Rhino document.")
    return doc


def install():
    """No menu API in Rhino; say so once instead of failing silently."""
    from .. import log
    log.warning(
        "Rhino builds its menus from a .rui workspace, not from Python, so no "
        "%s menu was added. Run the actions from the Python editor: "
        "import flow_dcc; flow_dcc.adapter().action_version_up() -- "
        "or bind them to aliases, see startup/rhino/README.txt.", MENU_NAME)
    return False


def save_scene(path):
    """SaveAs reports success as a bool, not an exception."""
    if not _doc().SaveAs(path):
        raise RuntimeError("Rhino refused to save {0}".format(path))
    return path


def open_scene(path):
    if _doc().Modified and not common.discard_ok(sys.modules[__name__], path):
        return None
    if not _rhino().RhinoDoc.OpenFile(path):
        raise RuntimeError("Rhino refused to open {0}".format(path))
    return path


def pick_file(files):
    """Rhino's own list box: Rhino on Windows has no PySide to import."""
    from .. import picker
    labels = [picker.label(row) for row in picker.rows(files)]
    chosen = _rs().ListBox(labels, "Version   Artist   Saved",
                           "Flow: Load File")
    return files[labels.index(chosen)]["path"] if chosen else None


def current_scene():
    return _doc().Path or ""


def message(text):
    sys.stdout.write("Flow: {0}\n".format(text))
    _rs().MessageBox(text, 0, "Flow")


def confirm(text):
    """MessageBox(..., 1) is OK/Cancel and returns 1 for OK."""
    return _rs().MessageBox(text, 1, "Flow") == 1


def ask_path(start_dir, extension, suggested=""):
    chosen = _rs().SaveFileName(
        "Flow: Save As",
        "Rhino Models (*{0})|*{0}||".format(extension or ".3dm"),
        start_dir, suggested)
    return chosen or None


# -- USD (flow_dcc/usd.py) ------------------------------------------------
#
# Rhino 8 reads and writes .usd by extension. There is no menu (see install),
# so run these from the Python editor:
#     import flow_dcc; flow_dcc.adapter().action_publish_usd()

def export_usd(path, root_prim=None):
    """Export the selected objects. Rhino chooses USD from the extension.

    `root_prim` is not applied: Rhino has no pxr to check the result either,
    so an asset published from Rhino should be checked in usdview.
    """
    doc = _doc()
    if not list(doc.Objects.GetSelectedObjects(False, False)):
        raise RuntimeError(
            "Select what this department publishes and publish again.")
    if not doc.ExportSelected(path):
        raise RuntimeError("Rhino refused to export {0} -- USD export needs "
                           "Rhino 8 or later.".format(path))
    return path


def load_usd_stage(path):
    """Import the stage into the open model -- a snapshot, not a live link."""
    if not _doc().Import(path):
        raise RuntimeError("Rhino could not import {0}".format(path))
    return path


# -- menu actions ---------------------------------------------------------

common.bind(sys.modules[__name__])
