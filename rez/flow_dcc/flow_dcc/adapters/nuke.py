"""Nuke adapter: the Flow menu on Nuke's menu bar.

The `nuke` module is imported inside the functions so this file can be read,
imported and tested outside Nuke.
"""

import logging
import os
import shutil
import sys

from . import MENU_NAME, actions, common
from .. import (context, lock, paths, picker, plates, publish, review,
                shotgrid, version_publish)

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
    menu.addSeparator()
    menu.addCommand("Open Sequence Review...", open_sequence_review)
    menu.addCommand("Create Annotation File", create_annotation_file)
    menu.addCommand("Retry Pending Notes", retry_pending_notes)
    menu.addCommand("Retry Pending Publishes", retry_pending_publishes)
    nuke.addOnScriptLoad(_on_load)
    nuke.addOnScriptSave(_on_save)
    nuke.addBeforeRender(_before_render)
    _after_startup(setup_empty_script)
    _after_startup(_resume_publishes)
    _after_startup(_watch_idle)
    return True


# -- idle script lock (flow_dcc/lock.py) ------------------------------------

_idle = {"active": 0.0, "released": None, "told": None}


def _watch_idle(every=60000):
    """Keep the open script's lock fresh while the artist works; give it up
    after lock.IDLE_MINUTES without a key or a click, so a lead can open the
    script (Nuke's own autosave holds what was not saved)."""
    try:
        from PySide6.QtCore import QEvent, QObject, QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        from PySide2.QtCore import QEvent, QObject, QTimer
        from PySide2.QtWidgets import QApplication
    import time
    kinds = (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.Wheel)

    class Activity(QObject):
        def eventFilter(self, obj, event):
            if event.type() in kinds:
                _idle["active"] = time.time()
            return False

    _idle["active"] = time.time()
    watcher = Activity()
    QApplication.instance().installEventFilter(watcher)
    timer = QTimer()
    timer.timeout.connect(lambda: _idle_tick(time.time()))
    _watching.extend([watcher, timer])
    timer.start(every)


def _idle_tick(now):
    path = current_scene()
    if not path:
        return
    idle = now - _idle["active"] > lock.IDLE_MINUTES * 60
    if idle:
        if _idle["released"] != path:
            lock.release(path)
            _idle["released"] = path
            log.info("idle %d min: gave up the lock on %s", lock.IDLE_MINUTES,
                     path)
        return
    if lock.touch(path):
        return
    # Back from idle, or a reviewer took the file: take it back if free.
    info = lock.holder(path)
    if info is None:
        try:
            lock.take(path)
            lock.keep_only(path)
            _idle["released"] = None
            return
        except Exception:
            info = lock.holder(path) or {}
    if _idle["told"] != (path, info.get("user")):
        _idle["told"] = (path, info.get("user"))
        message("{0} was opened by {1} while this Nuke was idle.\n\nDo not "
                "save over it: Save As a new version to keep your changes."
                .format(os.path.basename(path), lock.describe(info)))


def _on_save():
    """Nuke's own Save As: render where the new name says, and lock it."""
    _retarget_writes(current_scene())
    common.opening(sys.modules[__name__], current_scene())


def _on_load():
    """Lock every script Nuke opens, File menu included. One someone else
    has open is cleared again -- Nuke has no way to cancel the load. One
    another artist saved is offered as a copy of the current artist's own,
    after the load: Nuke cannot save from inside it."""
    module = sys.modules[__name__]
    path = current_scene()
    if not common.opening(module, path):
        _after_startup(_clear)                  # not from inside the load
    elif path:
        _after_startup(lambda: common.offer_own_copy(module, path))


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
    """Nuke opened empty on a shot task.

    The shot's first Nuke script (no department folder has one yet) is built
    and saved straight away: see new_script(). After that every launch asks,
    in start_dialog(), which department and script to open, or to start a
    new one. An asset task, which has no plate, goes through common.start().
    """
    if current_scene() or _has_nodes():
        return
    ctx = context.get()
    module = sys.modules[__name__]
    if ctx.entity_type != "Shot" or not ctx.entity_root or not ctx.has_task:
        return common.start(module)
    if getattr(module, "_flow_started", False):
        return
    module._flow_started = True
    try:
        if any(paths.department_files(d, ctx) for d in departments(ctx)):
            path = start_dialog()
        else:
            path = new_script()
        if path:
            _keep_open(path)
    except Exception as e:                       # never cost the artist Nuke
        log.warning("shot setup skipped: %s", e)


def departments(ctx=None):
    """The dialog's department list: the configured ones, plus the task's."""
    ctx = ctx or context.get()
    extra = [ctx.department] if ctx.department and \
        ctx.department not in paths.DEPARTMENTS else []
    return paths.DEPARTMENTS + extra


def start_dialog():
    """Pick a department, then open one of its scripts or start a new one.

    The picked department is kept for the session (FLOW_DEPARTMENT), so
    Save, Version Up, Publish and the Write all follow it.
    """
    ctx = context.get()
    choice = picker.ask_start(departments(ctx), ctx.department,
                              lambda d: paths.department_files(d, ctx))
    if not choice:
        return None
    action, department, path = choice
    os.environ["FLOW_DEPARTMENT"] = department
    if action == "open":
        return common.open_path(sys.modules[__name__], path)
    return new_script()


def new_script():
    """Load the plate and movie, add a Write, and save it as this artist's
    next version in the department's work folder (v0001 for an artist new
    to it), so the artist starts comping, not wiring."""
    nuke = _nuke()
    module = sys.modules[__name__]
    if current_scene() or _has_nodes():
        if nuke.root().modified() and not common.discard_ok(
                module, "a new script"):
            return None
        nuke.scriptClear()
        lock.release_all()                      # nothing is open now
    # Saved first, so the Write is named after the real script.
    path = publish.save_next_version(module)
    setup_shot(quiet=True)
    save_scene(path)
    log.info("created %s", path)
    return path


def action_load_file():
    """The menu's Load File...: the launch dialog, on a shot task."""
    ctx = context.get()
    if ctx.entity_type != "Shot" or not ctx.has_task:
        return common.load_file(sys.modules[__name__])
    try:
        return start_dialog()
    except Exception as e:
        message("Load File failed: {0}".format(e))
        return None


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
    write.addKnob(nuke.Tab_Knob("flow", "Flow"))
    follow = nuke.Boolean_Knob(FOLLOW, "Follow script version")
    follow.setValue(True)
    follow.setTooltip("Ticked: saving a new script version renders to the "
                      "new version. Unticked: this Write keeps its path.")
    write.addKnob(follow)
    if "create_directories" in write.knobs():
        write["create_directories"].setValue(True)
    if read:
        write.setXYpos(read.xpos(), read.ypos() + 200)
    return read


def save_scene(path):
    _retarget_writes(path)
    _nuke().scriptSaveAs(path, overwrite=1)
    return path


FOLLOW = "flow_follow_version"


def _retarget_writes(script):
    """Point Writes that render into a render folder at script's own
    subfolder, so Version Up renders v0002 beside v0001, not over it.

    A Write with "Follow script version" unticked keeps its path; one made
    before the checkbox existed follows. Writes under another department's
    render folder, or the pre-for_approval layout, move to this one's.
    """
    roots = plates.render_roots()
    if not roots or not script:
        return
    target = plates.write_path(script=script)
    for node in _nuke().allNodes("Write"):
        if FOLLOW in node.knobs() and not node[FOLLOW].value():
            continue
        current = (node["file"].value() or "").replace("\\", "/")
        if any(current.startswith(root + "/") for root in roots):
            node["file"].setValue(target)


def _before_render():
    """A Write rendering into <name>/for_approval/ gets the script it was
    rendered from copied into <name>/workfile/. Never stops the render."""
    try:
        node = _nuke().thisNode()
        snapshot_workfiles(current_scene(), [node["file"].evaluate()])
    except Exception as e:
        log.warning("workfile copy skipped: %s", e)


def snapshot_workfiles(scene, frames=None):
    """Save the script and copy it into the workfile/ folder of each render
    path in frames (every Write's, by default). Deadline calls this too."""
    if not scene:
        return []
    nuke = _nuke()
    if frames is None:
        frames = [n["file"].evaluate() for n in nuke.allNodes("Write")
                  if not n["disable"].value()]
    targets = set(filter(None, (plates.workfile_for(f or "", scene)
                                for f in frames)))
    if targets and nuke.root().modified():
        nuke.scriptSave()                       # the copy matches the render
    for target in sorted(targets):
        folder = os.path.dirname(target)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        shutil.copy2(scene, target)
        log.info("workfile %s", target)
    return sorted(targets)


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


# -- sequence review (flow_dcc/review.py) ---------------------------------

REVIEW_COLUMNS = 4
REVIEW_STEP = (700, 600)                # x, y between two shots' backdrops

_BEFORE = "import flow_dcc.adapters.nuke as f; f.annotation_before_render()"
_AFTER = "import flow_dcc.adapters.nuke as f; f.annotation_after_render()"


def _ask_everything():
    """Open (and with which shots), or None on Cancel."""
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets
    box = QtWidgets.QMessageBox(QtWidgets.QApplication.activeWindow())
    box.setWindowTitle("Flow: Sequence Review")
    box.setText("Load the plate and movie of every shot you review in this "
                "sequence, each with an annotation Write.")
    check = QtWidgets.QCheckBox(
        "Load every shot in the sequence, not only the shots I review")
    check.setChecked(False)
    box.setCheckBox(check)
    box.setStandardButtons(QtWidgets.QMessageBox.Open |
                           QtWidgets.QMessageBox.Cancel)
    run = getattr(box, "exec", None) or box.exec_
    if run() != QtWidgets.QMessageBox.Open:
        return None
    return check.isChecked()


def open_sequence_review(everything=None):
    """Build the lead's review script for the launch shot's sequence."""
    nuke = _nuke()
    module = sys.modules[__name__]
    ctx = context.get()
    if ctx.entity_type != "Shot" or not review.sequence_root(ctx):
        return message("Launch Nuke from Flow on a shot task to review its "
                       "sequence.")
    if everything is None:
        everything = _ask_everything()
        if everything is None:
            return None
    try:
        rows = review.shots(shotgrid.connect(), ctx, everything)
    except Exception as e:
        return message("Could not read the sequence from ShotGrid:\n{0}"
                       .format(e))
    if not rows:
        return message("You are not a reviewer on any shot in this sequence. "
                       "Tick 'Load every shot' to see them all.")
    path = review.script_path(ctx)
    if not confirm("{0} shot(s) to load.\n\nThe review script will be saved "
                   "to:\n{1}\n\nContinue?".format(len(rows), path)):
        return None
    if current_scene() or _has_nodes():
        if nuke.root().modified() and not common.discard_ok(
                module, "the sequence review"):
            return None
        nuke.scriptClear()
        lock.release_all()
    for i, shot in enumerate(rows):
        _review_shot(shot, (i % REVIEW_COLUMNS) * REVIEW_STEP[0],
                     (i // REVIEW_COLUMNS) * REVIEW_STEP[1])
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    nuke.scriptSaveAs(path, overwrite=0)
    publish.hold(path)
    log.info("sequence review %s: %d shots", path, len(rows))
    return path


def _review_shot(shot, x, y):
    """One shot's backdrop: plate -> RotoPaint -> annotation Write, + movie."""
    nuke = _nuke()
    folder = shot["root"] + "/" + plates.PLATES
    plate = plates.find_plate(folder)
    read = nuke.nodes.Read(file=plate["path"]) if plate else None
    if read is not None and plate["first"] is not None:
        for knob in ("first", "origfirst"):
            read[knob].setValue(plate["first"])
        for knob in ("last", "origlast"):
            read[knob].setValue(plate["last"])
    paint = nuke.nodes.RotoPaint(inputs=[read] if read else [])
    write = nuke.nodes.Write(inputs=[paint])
    write.setName("Annotate_" + shot["code"])
    write["file_type"].setValue("jpeg")
    # Rendered where beforeRender says, at the moment the lead renders.
    write["file"].setValue(review.annotation_path(shot["code"],
                                                  shot["department"]))
    for name, value in (("flow_shot_id", shot["id"]),
                        ("flow_shot", shot["code"]),
                        ("flow_department", shot["department"])):
        knob = nuke.String_Knob(name, name)
        knob.setValue(str(value))
        knob.setVisible(False)
        write.addKnob(knob)
    write.addKnob(nuke.Multiline_Eval_String_Knob(
        "flow_note", "Note to artist"))
    write["beforeRender"].setValue(_BEFORE)
    write["afterRender"].setValue(_AFTER)
    nodes = [n for n in (read, paint, write) if n is not None]
    for i, node in enumerate(nodes):
        node.setXYpos(x + 40, y + 60 + i * 120)
    movie = plates.find_movie(folder)
    if movie and (not plate or movie["path"] != plate["path"]):
        nuke.nodes.Read(file=movie["path"]).setXYpos(x + 240, y + 60)
    nuke.nodes.BackdropNode(
        xpos=x, ypos=y, bdwidth=REVIEW_STEP[0] - 100,
        bdheight=REVIEW_STEP[1] - 120, note_font_size=42,
        label="{0}  ({1})".format(shot["code"],
                                  paths.department_code(shot["department"])))
    if not plate:
        log.warning("%s: no plate under %s", shot["code"], folder)
    return write


def annotation_before_render():
    """Fix this render's path: the time now, so a later round never
    overwrites this one, and make its folder."""
    node = _nuke().thisNode()
    path = review.annotation_path(node["flow_shot"].value(),
                                  node["flow_department"].value())
    node["file"].setValue(path)
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)


def annotation_after_render():
    """Post the rendered frames as a Note on the shot, or queue it."""
    node = _nuke().thisNode()
    folder = os.path.dirname(node["file"].value())
    images = sorted(os.path.join(folder, f).replace("\\", "/")
                    for f in os.listdir(folder) if f.lower().endswith(".jpg"))
    if not images:
        return
    entry = review.note_entry(
        {"id": int(node["flow_shot_id"].value()),
         "code": node["flow_shot"].value()},
        node["flow_department"].value(), images, node["flow_note"].value())
    posted, reason = review.send_or_queue(shotgrid.connect, entry)
    if posted:
        message("Annotations posted to {0} in ShotGrid:\n{1}".format(
            node["flow_shot"].value(), folder))
    else:
        message("The frames are rendered in\n{0}\n\nShotGrid could not be "
                "reached ({1}), so the Note is queued. Post it later with "
                "{2} > Retry Pending Notes.".format(folder, reason, MENU_NAME))


def retry_pending_notes():
    sent, left, error = review.retry(shotgrid.connect)
    if not sent and not left:
        return message("No annotation Notes are waiting.")
    text = "{0} Note(s) posted to ShotGrid.".format(sent)
    if left:
        text += "\n{0} still waiting: {1}".format(left, error)
    return message(text)


def create_annotation_file():
    """The Viewer's current frame, as drawn, to a JPEG for internal notes:
    <project>/Reference/Annotated_notes/<read>_<frame>_v001.jpg. Not posted
    to ShotGrid: these are for iterating, not for review."""
    nuke = _nuke()
    viewer = nuke.activeViewer()
    node = viewer.node().input(viewer.activeInput() or 0) if viewer else None
    if node is None:
        return message("Connect the Viewer to the node to grab, then try "
                       "again.")
    read = _upstream_read(node)
    source = read["file"].value() if read else node.name()
    frame = int(nuke.frame())
    path = review.annotated_note_path(source, frame)
    if not path:
        return message("Launch Nuke from Flow on a task first: Flow needs "
                       "the project folder.")
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    write = nuke.nodes.Write(inputs=[node])
    try:
        write["file"].setValue(path)
        write["file_type"].setValue("jpeg")
        nuke.execute(write, frame, frame)
    except Exception as e:
        return message("Could not render the frame: {0}".format(e))
    finally:
        nuke.delete(write)
    log.info("annotation %s", path)
    return message("Saved\n{0}".format(path))


def _upstream_read(node, seen=None):
    """node if it is a Read, else the nearest Read above it (input 0 first),
    or None."""
    seen = seen if seen is not None else set()
    if node is None or node.name() in seen:
        return None
    seen.add(node.name())
    if node.Class() == "Read":
        return node
    for i in range(node.inputs()):
        found = _upstream_read(node.input(i), seen)
        if found is not None:
            return found
    return None


# -- Publish (flow_dcc/version_publish.py) --------------------------------

def action_publish():
    """Publish the open script's render: no questions asked.

    The script that rendered (its workfile/ copy) goes to the publish folder
    and ShotGrid; the movie, the Version, its upload and the mail happen in a
    background process, so the artist keeps working. A script with no render
    is not published: a Version without frames has nothing to review.
    """
    nuke = _nuke()
    ctx = context.get()
    scene = current_scene()
    if not scene or not ctx.has_task:
        return message("Open a script from Flow on a task, render it, then "
                       "Publish.")
    try:
        publish.validate(ctx)
        if nuke.root().modified():
            nuke.scriptSave()
        name = os.path.splitext(os.path.basename(scene))[0]
        folder = os.path.dirname(plates.write_path(ctx, scene))
        frames = plates.find_plate(folder)
        if not frames or frames["first"] is None:
            return message("{0} has no render yet. Render its Write to\n{1}"
                           "\nthen Publish.".format(name, folder))
        target = paths.publish_for(scene, ctx)
        if os.path.exists(target):
            return message("{0} is already published:\n{1}\n\nVersion Up, "
                           "render and publish again.".format(name, target))
        workfile = (snapshot_workfiles(scene, [frames["path"]]) or
                    [scene])[0]
        # Roto: the whole <name>/ folder goes to elements/roto/ as well.
        copied = plates.publish_copy(os.path.dirname(os.path.dirname(
            folder)), ctx)
        if copied:
            frames = plates.find_plate(
                copied + "/" + plates.FOR_APPROVAL + "/" + name)
        result = publish.publish_file(workfile, ctx)
        job = version_publish.entry(name, frames["path"], frames["first"],
                                    frames["last"], ctx)
        process = version_publish.start(job, nuke.EXE_PATH)
    except publish.PublishError as e:
        return message(str(e))
    except Exception as e:
        return message("Publish failed: {0}".format(e))
    _watch(job, process)
    text = ("Publishing {0}.\n\nThe script is published{1}. The movie is "
            "being made and uploaded to ShotGrid in the background: keep "
            "working. A mail goes to {2} when it is done.").format(
                name, "" if result.registered else
                " on disk (not in ShotGrid: {0})".format(
                    result.registration_error),
                job["email"] or "you")
    message(text)
    return job


def _watch(job, process, every=5000):
    """Tell the artist how the background publish ended."""
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    timer = QTimer()
    timer.setInterval(every)

    def check():
        if process.poll() is None:
            return
        timer.stop()
        _watching.remove(timer)
        if not os.path.isfile(job["_file"]):
            log.info("published %s", job["name"])
            return message("{0} is published to ShotGrid.".format(
                job["name"]))
        failed = version_publish.load(job["_file"])
        message("Publishing {0} stopped at {1}\n\nWhat is done stays done. "
                "Use {2} > Retry Pending Publishes to finish it.\nLog: {3}"
                .format(job["name"], failed.get("error") or "an unknown step",
                        MENU_NAME, os.path.splitext(job["_file"])[0] +
                        ".log"))

    timer.timeout.connect(check)
    _watching.append(timer)             # a QTimer nobody holds is collected
    timer.start()


_watching = []


def retry_pending_publishes(quiet=False):
    """Restart this machine's publishes whose worker failed or died."""
    nuke = _nuke()
    jobs = [j for j in version_publish.pending()
            if version_publish.stalled(j)]
    for job in jobs:
        job["error"] = ""
        _watch(job, version_publish.start(job, nuke.EXE_PATH))
    if jobs or not quiet:
        message("{0} publish(es) restarted in the background.".format(
            len(jobs)) if jobs else "No publishes are waiting.")
    return jobs


def _resume_publishes():
    try:
        retry_pending_publishes(quiet=True)
    except Exception as e:
        log.warning("could not resume publishes: %s", e)


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
