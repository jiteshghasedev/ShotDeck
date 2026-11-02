"""Silhouette adapter -- deliberately written against what is found at runtime.

The Silhouette build on the 5and8 farm has not been inspected yet, so nothing
here assumes a menu API, a module name, or a save function. `probe_silhouette.py`
in the package root prints what the installed version actually exposes; wire the
result into HOST_MODULES / SAVE_METHODS / _build_menu() once it has been run.

Until then this adapter still does useful work: the actions are importable and
callable from Silhouette's script console, and install() says exactly what it
could not find instead of failing silently or guessing.
"""

import sys

from . import ACTIONS, MENU_NAME, common

# Candidate module names, most likely first. Extend from the probe output --
# do not guess new ones here.
HOST_MODULES = ["fx", "silhouette"]

# Save entry points to try, in order. Each is called as method(path).
SAVE_METHODS = ["saveProject", "save_project", "save"]

# Open entry points to try, in order, as SAVE_METHODS. Unconfirmed likewise.
OPEN_METHODS = ["loadProject", "openProject", "load_project", "open_project"]


def host():
    """The Silhouette Python module, or None when running outside it."""
    for name in HOST_MODULES:
        try:
            return __import__(name)
        except ImportError:
            continue
    return None


def install():
    """Build the menu if this build exposes one; report clearly if it does not."""
    module = host()
    if module is None:
        common_log("no Silhouette Python module found (tried {0}) — run "
                   "probe_silhouette.py inside Silhouette and extend "
                   "HOST_MODULES".format(", ".join(HOST_MODULES)))
        return False

    if not _build_menu(module):
        common_log(
            "loaded, but this Silhouette build's menu API has not been "
            "confirmed, so no {0} menu was added. The actions are available "
            "from the script console: "
            "import flow_dcc; flow_dcc.adapter().action_save_next_version()"
            .format(MENU_NAME))
        return False
    return True


def _build_menu(module):
    """Left unimplemented on purpose: see the module docstring.

    Returning False is the honest answer until the probe says which of
    Silhouette's APIs this version ships. Implement here, not in install().
    """
    return False


def save_scene(path):
    """Save through whichever documented method this build exposes."""
    module = host()
    if module is None:
        raise RuntimeError("Not running inside Silhouette.")

    for name in SAVE_METHODS:
        method = getattr(module, name, None)
        if callable(method):
            method(path)
            return path

    raise RuntimeError(
        "None of {0} exist on the Silhouette module ({1}). Run "
        "probe_silhouette.py and add the correct one to SAVE_METHODS."
        .format(", ".join(SAVE_METHODS), getattr(module, "__name__", "?")))


def open_scene(path):
    """Open through whichever method this build exposes, as save_scene()."""
    module = host()
    if module is None:
        raise RuntimeError("Not running inside Silhouette.")
    for name in OPEN_METHODS:
        method = getattr(module, name, None)
        if callable(method):
            method(path)
            return path
    raise RuntimeError(
        "None of {0} exist on the Silhouette module. Run probe_silhouette.py "
        "and add the correct one to OPEN_METHODS.".format(
            ", ".join(OPEN_METHODS)))


def current_scene():
    module = host()
    project = getattr(module, "activeProject", None) if module else None
    if callable(project):
        project = project()
    return getattr(project, "path", "") or ""


def message(text):
    """Silhouette's console. No dialog is attempted -- that API is unconfirmed."""
    sys.stdout.write("Flow: {0}\n".format(text))


def confirm(text):
    """No confirmed dialog API, so a console prompt would block the UI.

    Returning True means Publish proceeds without asking here. The publish is
    still driven from the script console rather than a menu, which is its own
    confirmation -- nobody types the call by accident.
    """
    message(text)
    return True


def ask_path(start_dir, extension, suggested=""):
    """No confirmed file-dialog API. Callers get None, which cancels Save As."""
    message("Save As needs a file dialog, which this Silhouette build has not "
            "been confirmed to expose. Use Version Up, or run "
            "flow_dcc.publish.save_next_version() from the console.")
    return None


def common_log(text):
    from .. import log
    log.warning(text)


# -- menu actions ---------------------------------------------------------

common.bind(sys.modules[__name__])
