"""Silhouette adapter: the 5and8 menu on Silhouette's menu bar.

Started by startup/silhouette/flow_startup/__init__.py, which Silhouette
imports because Flow (or the flow_dcc rez package) lists that folder in
SFX_SCRIPT_IMPORTS. The `fx` module is imported inside the functions so this
file can be imported and tested outside Silhouette.

What it does, as the Nuke adapter does: on a shot task it opens the artist's
project or builds a new one with the plate loaded, saves under the pipeline
name (nuke/roto/scene/<artist>/<shot>_<artist>_roto_v0001.sfx), locks it, and
publishes the render with a movie and a ShotGrid Version. Export Splines
writes shapes to nuke/roto/splines/.

The fx calls are the ones the Silhouette Scripting Guide and the AYON
Silhouette integration use: fx.loadProject, fx.activeProject().save(path),
project.path, project.is_modified, fx.Project, fx.setActiveProject,
fx.Source("plate.[1001-1050].exr"), project.addItem, fx.io_modules[...]
.export(path), fx.selection, fx.displayInformation, fx.askQuestion.

shortcut: the plate lands in the Project bin, not in a session -- making a
session from a Source (tools.session.SessionBuilder) and setting an Output
node's path are undocumented; wire both once their scripts are read.
"""

import logging
import os
import shutil
import sys

from . import MENU_NAME, actions, common
from .. import context, paths, picker, plates, publish

log = logging.getLogger("flow")

MOVIE_TOOL = "ffmpeg"           # no Nuke here: the worker makes it in ffmpeg

# Silhouette's shape exporters (fx.io_modules), and the file each writes.
SPLINE_FORMATS = [("Nuke 9+ Shapes", ".nk"), ("Silhouette Shapes", ".fxs"),
                  ("Nuke 6.2+ Shapes", ".nk")]


def _fx():
    import fx
    return fx


def _qt():
    try:
        from PySide2 import QtCore, QtWidgets       # what Silhouette ships
    except ImportError:
        from PySide6 import QtCore, QtWidgets
    return QtCore, QtWidgets


def _main_window():
    fx = _fx()
    window = getattr(getattr(fx, "ui", None), "mainWindow", None)
    if callable(window):
        window = window()
    if window is not None:
        return window
    try:
        import tools.window                         # Silhouette's own helper
        return tools.window.get_main_window()
    except Exception:
        _, QtWidgets = _qt()
        return next((w for w in QtWidgets.QApplication.topLevelWidgets()
                     if isinstance(w, QtWidgets.QMainWindow)), None)


def _defer(func, delay=0):
    QtCore, _ = _qt()
    QtCore.QTimer.singleShot(delay, func)


# -- install --------------------------------------------------------------

def install():
    """Add the menu once the window exists. No-op without a GUI."""
    if not getattr(_fx(), "gui", False):
        return False
    _defer(_build_menu)
    _defer(_start, 500)
    _defer(_resume_publishes, 1000)
    return True


def _build_menu(tries=20):
    window = _main_window()
    if window is None:                  # not up yet: try again shortly
        if tries:
            _defer(lambda: _build_menu(tries - 1), 500)
        else:
            log.warning("no Silhouette main window: %s menu not built",
                        MENU_NAME)
        return
    module = sys.modules[__name__]
    menu = window.menuBar().addMenu(MENU_NAME)
    entries = list(actions(module)) + [
        (None, None),
        ("Load Plate", "load_plate"),
        ("Render Path...", "show_render_path"),
        ("Export Splines...", "export_splines"),
        ("Task Panel...", "action_task_panel"),
        (None, None),
        ("Retry Pending Publishes", "retry_pending_publishes"),
    ]
    for label, attr in entries:
        if label is None:
            menu.addSeparator()
            continue
        action = menu.addAction(label)
        action.triggered.connect(
            lambda _=False, f=getattr(module, attr): _safe(f))
    module._flow_menu = menu            # keep it alive


def _safe(func):
    try:
        func()
    except Exception as e:
        log.exception("Flow action failed")
        message("{0} failed: {1}".format(
            getattr(func, "__name__", "Flow action"), e))


# -- the shot's project at launch -------------------------------------------

def _start():
    """Silhouette opened on a task: open the artist's project or start one.

    A shot task asks which department and project to open when there is
    one already (the same dialog as Nuke), or builds the first project with
    the plate loaded. Anything else goes through common.start().
    """
    module = sys.modules[__name__]
    ctx = context.get()
    if current_scene() or ctx.entity_type != "Shot" or \
            not ctx.entity_root or not ctx.has_task:
        return common.start(module)
    if getattr(module, "_flow_started", False):
        return None
    module._flow_started = True
    try:
        if any(paths.department_files(d, ctx) for d in departments(ctx)):
            return start_dialog()
        return new_project()
    except Exception as e:                       # never cost the artist the app
        log.warning("shot setup skipped: %s", e)


def departments(ctx=None):
    """The dialog's department list: the configured ones, plus the task's."""
    ctx = ctx or context.get()
    extra = [ctx.department] if ctx.department and \
        ctx.department not in paths.DEPARTMENTS else []
    return paths.DEPARTMENTS + extra


def start_dialog():
    """Pick a department, then open one of its projects or start a new one."""
    ctx = context.get()
    choice = picker.ask_start(departments(ctx), ctx.department,
                              lambda d: paths.department_files(d, ctx))
    if not choice:
        return None
    action, department, path = choice
    os.environ["FLOW_DEPARTMENT"] = department
    if action == "open":
        return common.open_path(sys.modules[__name__], path)
    return new_project()


def new_project():
    """A new project, saved as this artist's next version, plate loaded."""
    fx = _fx()
    module = sys.modules[__name__]
    if current_scene() and is_modified() and \
            not common.discard_ok(module, "a new project"):
        return None
    fx.setActiveProject(fx.Project())
    path = publish.save_next_version(module)
    load_plate(quiet=True)
    save_scene(path)
    log.info("created %s", path)
    return path


def action_load_file():
    """The menu's Load File...: the launch dialog, on a shot task."""
    ctx = context.get()
    if ctx.entity_type != "Shot" or not ctx.has_task:
        return common.load_file(sys.modules[__name__])
    return start_dialog()


# -- plate -----------------------------------------------------------------

def load_plate(quiet=False):
    """Add the shot's newest plate (and its movie) to the Project bin."""
    fx = _fx()
    ctx = context.get()
    folder = plates.plates_dir(ctx)
    plate = plates.find_plate(folder)
    movie = plates.find_movie(folder)
    items = [p for p in (plate, movie) if p]
    if plate and movie and movie["path"] == plate["path"]:
        items = [plate]
    if not items:
        text = "No plate (image sequence or movie) under {0}".format(folder)
        return log.warning(text) if quiet else message(text)
    project = fx.activeProject()
    if project is None:
        project = fx.Project()
        fx.setActiveProject(project)
    have = set()
    for source in getattr(project, "sources", None) or []:
        try:
            have.add(source.property("path").value)
        except Exception:
            pass
    added = []
    for item in items:
        path = plates.bracket_range(item["path"], item["first"], item["last"])
        if path in have:
            continue
        source = fx.Source(path)
        source.label = os.path.basename(item["path"]).split(".")[0]
        project.addItem(source)
        added.append(path)
        log.info("source %s", path)
    if not quiet:
        message("Added to the project:\n{0}\n\nRight-click the plate in the "
                "Project window to make a session from it.".format(
                    "\n".join(added) or "(already there)"))
    return added


def show_render_path():
    """Where this project's renders go, copied to the clipboard."""
    scene = current_scene()
    if not scene:
        return message("Save the project from Flow first.")
    folder = os.path.dirname(plates.write_path(context.get(), scene))
    name = os.path.splitext(os.path.basename(scene))[0]
    _, QtWidgets = _qt()
    QtWidgets.QApplication.clipboard().setText(folder)
    return message("Render into this folder (copied to the clipboard):\n{0}"
                   "\n\nas {1}.####.exr, then {2} > Publish...".format(
                       folder, name, MENU_NAME))


# -- splines (lead feedback 11.5) ------------------------------------------

def export_splines():
    """Export the selected shapes to nuke/roto/splines/<project name>.<ext>,
    in the format the artist picks."""
    fx = _fx()
    ctx = context.get()
    scene = current_scene()
    if not scene or not ctx.entity_root:
        return message("Open a project from Flow on a shot task first.")
    if not fx.selection():
        return message("Select the shapes (or layers) to export, then Export "
                       "Splines again.")
    _, QtWidgets = _qt()
    labels = [label for label, _ in SPLINE_FORMATS]
    label, ok = QtWidgets.QInputDialog.getItem(
        _main_window(), "Flow: Export Splines", "Format:", labels, 0, False)
    if not ok:
        return None
    ext = dict(SPLINE_FORMATS)[label]
    path = plates.splines_path(ctx, scene, ext)
    if os.path.exists(path) and not confirm(
            "{0} exists. Replace it?".format(path)):
        return None
    exporter = fx.io_modules[label]
    if not getattr(exporter, "can_export", True):
        return message("{0} cannot export the current selection.".format(
            label))
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    exporter.export(path)
    return message("Splines exported:\n{0}".format(path))


# -- publish (adapters/common.publish_render) --------------------------------

def publish_runner():
    """A plain Python runs the background publish; ffmpeg makes the movie."""
    return [shutil.which("python3") or "/usr/bin/python3"]


def action_publish():
    return common.publish_render(sys.modules[__name__])


def retry_pending_publishes(quiet=False):
    return common.retry_publishes(sys.modules[__name__], quiet)


def _resume_publishes():
    try:
        retry_pending_publishes(quiet=True)
    except Exception as e:
        log.warning("could not resume publishes: %s", e)


# -- host primitives used by common -----------------------------------------

def save_scene(path):
    """Save the project to path and carry on working in it there."""
    fx = _fx()
    project = fx.activeProject()
    if project is None:
        project = fx.Project()
        fx.setActiveProject(project)
    project.save(path)
    if _norm(current_scene()) != _norm(path):
        fx.loadProject(path)    # save(path) may leave the old one active
    return path


def open_scene(path):
    if is_modified() and not common.discard_ok(sys.modules[__name__], path):
        return None
    _fx().loadProject(path)
    return path


def current_scene():
    """The project folder (X.sfx). project.path may name the project.sfx
    file inside it."""
    project = _fx().activeProject()
    path = (getattr(project, "path", "") or "").replace("\\", "/")
    if os.path.basename(path) == "project.sfx":
        path = os.path.dirname(path)
    return path


def is_modified():
    project = _fx().activeProject()
    return bool(getattr(project, "is_modified", False))


def message(text):
    sys.stdout.write("Flow: {0}\n".format(text))
    try:
        _fx().displayInformation(text, "Flow")
    except Exception:
        _, QtWidgets = _qt()
        QtWidgets.QMessageBox.information(_main_window(), "Flow", text)


def confirm(text):
    try:
        return bool(_fx().askQuestion(text, "Flow", "Yes", "No"))
    except Exception:
        _, QtWidgets = _qt()
        box = QtWidgets.QMessageBox
        return box.question(_main_window(), "Flow", text,
                            box.Yes | box.No, box.No) == box.Yes


def ask_path(start_dir, extension, suggested=""):
    _, QtWidgets = _qt()
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        _main_window(), "Flow: Save As", start_dir + "/" + suggested,
        "Silhouette project (*{0})".format(extension or ".sfx"))
    return path or None


def _norm(path):
    return os.path.normpath(path or "").replace("\\", "/").rstrip("/")


# -- menu actions ---------------------------------------------------------

common.bind(sys.modules[__name__])
