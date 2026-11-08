"""Nuke adapter: the Flow menu on Nuke's menu bar.

The `nuke` module is imported inside the functions so this file can be read,
imported and tested outside Nuke.
"""

import logging
import os
import sys

from . import MENU_NAME, actions, common
from .. import context, lock, paths, plates, publish

# Nuke shows the logger in its terminal; a bare sys.stdout.write may not.
log = logging.getLogger("flow")


def _nuke():
    import nuke
    return nuke


def install():
    """Add the Flow menu. No-op in a terminal session, which has no menus."""
    nuke = _nuke()
    if nuke.env.get("gui") is False:
        return False

    menu = nuke.menu("Nuke").addMenu(MENU_NAME)
    module = sys.modules[__name__]
    for label, attr in actions(module):
        if label is None:
            menu.addSeparator()
            continue
        menu.addCommand(label, getattr(module, attr))
    menu.addSeparator()
    menu.addCommand("Load Plate + Write", setup_shot)
    menu.addCommand("Task Panel...", module.action_task_panel)
    nuke.addOnScriptLoad(_on_load)
    nuke.addOnScriptSave(_on_save)
    _after_startup(setup_empty_script)
    return True


def _on_save():
    """Nuke's own Save As: render where the new name says, and lock it."""
    _retarget_writes(current_scene())
    common.opening(sys.modules[__name__], current_scene())


def _on_load():
    """Lock every script Nuke opens, File menu included. One someone else
    has open is cleared again -- Nuke has no way to cancel the load."""
    if not common.opening(sys.modules[__name__], current_scene()):
        _after_startup(_clear)                  # not from inside the load


def _clear():
    _nuke().scriptClear()
    lock.release_all()                          # nothing is open now


def _after_startup(func):
    """Run func once Nuke has finished starting, i.e. after any script named
    on the command line is open. menu.py runs before that."""
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    QTimer.singleShot(0, func)


# -- shot setup -----------------------------------------------------------

def setup_empty_script():
    """Nuke opened empty on a shot task where this artist has no work file
    yet: load the plate and movie, add a Write, and save it as their v0001
    in the task's work folder, so the artist starts comping, not wiring.

    An artist with a work file on the task gets their newest one opened
    instead, and an asset task, which has no plate, goes the same way --
    see common.start().
    """
    if current_scene() or _has_nodes():
        return
    ctx = context.get()
    module = sys.modules[__name__]
    if ctx.entity_type != "Shot" or not ctx.entity_root or \
            not ctx.has_task or paths.artist_files(ctx):
        return common.start(module)
    module._flow_started = True
    try:
        # Saved first, so the Write is named after the real script.
        path = publish.save_next_version(module, ctx)
        setup_shot(quiet=True)
        save_scene(path)
        log.info("created %s", path)
        _keep_open(path)
    except Exception as e:                       # never cost the artist Nuke
        log.warning("shot setup skipped: %s", e)


def _keep_open(path, delays=(1000, 3000, 6000, 10000)):
    """Reopen path if Nuke drops it while it is still starting up.

    Nuke 16 can finish its own startup after the first Qt tick and reset the
    live script to a new, untitled one; the saved file is fine, so open it.

    shortcut: polls for 10s; hook Nuke's own startup-finished signal instead
    if one turns up.
    """
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer

    def check():
        if not current_scene() and not _has_nodes() and \
                os.path.isfile(path):
            log.warning("Nuke reset the new script at startup; reopening %s",
                        path)
            _nuke().scriptOpen(path)

    for delay in delays:
        QTimer.singleShot(delay, check)


def _named_format(read):
    """The Read's format by name. An unnamed one on the root is saved
    without a name, and Nuke warns "not in named format list" on load."""
    nuke = _nuke()
    fmt = read.format()
    size = (fmt.width(), fmt.height(), fmt.pixelAspect())
    for named in nuke.formats():
        if named.name() and \
                (named.width(), named.height(), named.pixelAspect()) == size:
            return named.name()
    name = "Flow_{0}x{1}".format(*size[:2])
    nuke.addFormat("{0} {1} {2} {3}".format(size[0], size[1], size[2], name))
    return name


def _has_nodes():
    """Anything but the Viewer a fresh GUI session starts with."""
    return any(n.Class() != "Viewer" for n in _nuke().allNodes())


def setup_shot(quiet=False):
    """Read the newest plate, match the script to it, and add a Write."""
    nuke = _nuke()
    ctx = context.get()
    folder = plates.plates_dir(ctx)
    plate = plates.find_plate(folder)
    read = None
    if plate:
        read = nuke.nodes.Read(file=plate["path"])
        if plate["first"] is not None:
            for knob in ("first", "origfirst"):
                read[knob].setValue(plate["first"])
            for knob in ("last", "origlast"):
                read[knob].setValue(plate["last"])
            root = nuke.root()
            root["first_frame"].setValue(plate["first"])
            root["last_frame"].setValue(plate["last"])
        nuke.root()["format"].setValue(_named_format(read))
        log.info("plate %s", plate["path"])
    elif quiet:
        log.warning("no plate (image sequence or movie) under %s", folder)
    else:
        message("No plate found under {0}".format(folder))

    movie = plates.find_movie(folder)
    if movie and (not plate or movie["path"] != plate["path"]):
        mov = nuke.nodes.Read(file=movie["path"])
        if read:
            mov.setXYpos(read.xpos() + 150, read.ypos())
        log.info("movie %s", movie["path"])

    path = plates.write_path(ctx, current_scene())
    if not path:
        return read
    write = nuke.nodes.Write(inputs=[read] if read else [])
    write["file"].setValue(path)
    write["file_type"].setValue("exr")
    if "create_directories" in write.knobs():
        write["create_directories"].setValue(True)
    if read:
        write.setXYpos(read.xpos(), read.ypos() + 200)
    return read


def save_scene(path):
    _retarget_writes(path)
    _nuke().scriptSaveAs(path, overwrite=1)
    return path


def _retarget_writes(script):
    """Point Writes that render into the render folder at script's own
    subfolder, so Version Up renders v0002 beside v0001, not over it."""
    folder = plates.renders_dir()
    if not folder or not script:
        return
    target = plates.write_path(script=script)
    for node in _nuke().allNodes("Write"):
        current = node["file"].value() or ""
        if os.path.dirname(os.path.dirname(current)) == folder:
            node["file"].setValue(target)


def open_scene(path):
    nuke = _nuke()
    if nuke.root().modified() and not common.discard_ok(
            sys.modules[__name__], path):
        return None
    nuke.scriptClear()       # scriptOpen over a non-empty script starts a 2nd Nuke
    nuke.scriptOpen(path)
    return path


def current_scene():
    """An untitled script is named "Root", which is not a file to save over."""
    name = _nuke().root().name() or ""
    return "" if name == "Root" else name


def message(text):
    """nuke.message is modal; the terminal gets a copy for the launch log."""
    sys.stdout.write("Flow: {0}\n".format(text))
    _nuke().message(text)


def confirm(text):
    return bool(_nuke().ask(text))


def ask_path(start_dir, extension, suggested=""):
    chosen = _nuke().getFilename("Flow: Save As",
                                 "*{0}".format(extension or ""),
                                 default=start_dir + "/" + suggested)
    return chosen or None


# -- Deadline -------------------------------------------------------------

def frame_range():
    root = _nuke().root()
    return int(root.firstFrame()), int(root.lastFrame())


def deadline_plugin_info(scene):
    """Keys only Nuke knows.

    A selected Write node narrows the job to that one, which is what an artist
    means by selecting it. Nothing selected renders every Write, which is what
    Nuke itself does.
    """
    nuke = _nuke()
    info = {
        "Version": ".".join(nuke.NUKE_VERSION_STRING.split(".")[:2]),
        "BatchMode": True,
        "NukeX": bool(nuke.env.get("nukex")),
    }
    selected = [node.name() for node in nuke.selectedNodes("Write")]
    if selected:
        info["WriteNode"] = selected[0]
    return info


# -- USD (flow_dcc/usd.py) ------------------------------------------------

def export_usd(path, root_prim=None):
    """Write the selected 3D node's scene through a throwaway WriteGeo.

    For what Nuke contributes to a shot -- a solved or tracked camera, cards.
    `root_prim` is not applied: Nuke names prims after its nodes, and
    usd.check_layer refuses an asset layer that is not under /<asset>.
    """
    nuke = _nuke()
    source = nuke.selectedNodes()
    if not source:
        raise RuntimeError(
            "Select the 3D node to publish (a Camera or a Scene) and publish "
            "again.")
    write = nuke.nodes.WriteGeo(inputs=[source[0]])
    try:
        write["file"].setValue(path)
        write["file_type"].setValue("usd")
        first, last = frame_range()
        nuke.execute(write, first, last)
    finally:
        nuke.delete(write)
    return path


def load_usd_stage(path):
    """A ReadGeo on the stage, reused if one already reads it."""
    nuke = _nuke()
    for node in nuke.allNodes("ReadGeo2"):
        if node["file"].value() == path:
            return node
    node = nuke.nodes.ReadGeo2()
    node["file"].setValue(path)
    return node


# -- menu actions ---------------------------------------------------------

common.bind(sys.modules[__name__])
