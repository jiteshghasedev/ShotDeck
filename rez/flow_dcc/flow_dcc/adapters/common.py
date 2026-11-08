"""What the menu actions actually do, written once for every host.

Each function takes the adapter module and calls back into the handful of
things that are genuinely host-specific: save_scene(), open_scene(),
current_scene(), message(), confirm(), and ask_path() -- plus pick_file()
for a host with no Qt to show picker.ask().
"""

import functools
import os
import subprocess
import sys

from .. import (context, deadline, lock, paths, picker, publish, usd,
                versioning)


def save(adapter):
    """Save over the open scene, or fall through to Version Up if unsaved.

    Saving an untitled scene has no obvious destination, and silently picking
    one is how work ends up outside the pipeline -- so route it to the action
    that does know where scenes go.
    """
    current = adapter.current_scene()
    if not current:
        adapter.message(
            "This scene has never been saved, so Flow does not know which "
            "file to save over. Using Version Up instead.")
        return version_up(adapter)

    if not opening(adapter, current):     # opened behind Flow's back
        return None
    try:
        adapter.save_scene(current)
    except Exception as e:
        adapter.message("Save failed: {0}".format(e))
        return None

    adapter.message("Saved {0}".format(current))
    return current


def save_as(adapter):
    """Save to a path the artist picks, pre-filled with the correct name.

    The dialog opens on the task's work folder with the next pipeline name
    already in it, so the convention is what an artist gets by pressing
    Enter -- Save As is for the exception, not for naming.
    """
    start = paths.work_dir() or os.path.expanduser("~")
    try:
        suggested = os.path.basename(versioning.next_scene_path())
    except ValueError:
        suggested = ""                  # no task: let the host decide

    path = adapter.ask_path(start, paths.extension(), suggested)
    if not path:
        return None                     # cancelled

    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)

    if not claim(adapter, path):          # saving over someone's open file
        return None
    try:
        adapter.save_scene(path)
    except Exception as e:
        lock.release(path)
        adapter.message("Save As failed: {0}".format(e))
        return None
    lock.keep_only(path)

    if not _under(path, paths.work_dir()):
        adapter.message(
            "Saved {0}\n\nNote: this is outside the task's work folder, so "
            "Flow will not count it when working out the next version."
            .format(path))
    else:
        adapter.message("Saved {0}".format(path))
    return path


def version_up(adapter):
    """Save the open scene as the next version in the task's work folder."""
    try:
        path = publish.save_next_version(adapter)
    except ValueError as e:                  # launched without a task
        adapter.message(str(e))
        return None
    except Exception as e:
        adapter.message("Version Up failed: {0}".format(e))
        return None

    adapter.message("Saved {0}".format(path))
    return path


def publish_scene(adapter):
    """Confirm the destination, then run the publish pipeline."""
    try:
        work_path, publish_path, version = publish.prepare()
    except publish.PublishError as e:
        adapter.message(str(e))
        return None

    ctx = context.get()
    if not adapter.confirm(
            "Publish v{0:03d} of {1} / {2}?\n\n"
            "Work file   {3}\nPublish     {4}\n\n"
            "The scene will be saved first."
            .format(version, ctx.entity_name, ctx.task_name,
                    work_path, publish_path)):
        return None

    try:
        result = publish.publish(adapter)
    except publish.PublishError as e:
        adapter.message(str(e))
        return None
    except Exception as e:
        adapter.message("Publish failed: {0}".format(e))
        return None

    adapter.message(result.summary())
    return result


def submit_to_deadline(adapter):
    """Save the scene, confirm the job, hand it to the farm.

    The save is not optional and not a version up: the farm renders the file
    that is on disk, so submitting without saving queues yesterday's scene --
    the one bug in a submitter that costs a whole farm night. An unsaved scene
    has no file to save over, so that case routes to Version Up as Save does.
    """
    current = adapter.current_scene()
    if not current:
        if not version_up(adapter):
            return None
    else:
        try:
            adapter.save_scene(current)
        except Exception as e:
            adapter.message("Could not save before submitting: {0}".format(e))
            return None

    try:
        plugin_name, scene = deadline.validate(adapter)
        frames = deadline.frames(adapter)
    except deadline.DeadlineError as e:
        adapter.message(str(e))
        return None

    ctx = context.get()
    if not adapter.confirm(
            "Submit {0} to Deadline?\n\n"
            "Plugin      {1}\nFrames      {2}\nPool        {3}\n"
            "Scene       {4}"
            .format(ctx.task_name or "this scene", plugin_name, frames,
                    deadline.POOL, scene)):
        return None

    try:
        job = deadline.submit(adapter)
    except deadline.DeadlineError as e:
        adapter.message(str(e))
        return None
    except Exception as e:
        adapter.message("Submit to Deadline failed: {0}".format(e))
        return None

    adapter.message(job.summary())
    return job


def load_file(adapter):
    """Pick one of the task's work files -- any artist's -- and open it."""
    files = paths.task_files()
    if not files:
        adapter.message(
            "No work files for this task yet in\n{0}".format(
                paths.work_dir() or "(launched without a task)"))
        return None

    pick = getattr(adapter, "pick_file", None)
    if pick is None:
        try:
            path = picker.ask(files)
        except ImportError:
            adapter.message("No Qt in this host to show the file list.")
            return None
    else:
        path = pick(files)        # may answer later, through open_path()
    if not path:
        return None
    return open_path(adapter, path)


def open_path(adapter, path):
    """Lock path, then open it. Nothing is opened that someone else has."""
    if not claim(adapter, path):
        return None
    try:
        opened = adapter.open_scene(path)
    except Exception as e:
        adapter.message("Could not open {0}: {1}".format(path, e))
        opened = None
    if opened:
        lock.keep_only(path)
    else:
        lock.release(path)        # cancelled: the old scene is still open
    return opened


def claim(adapter, path):
    """Lock path for this session before it is opened or saved over.

    False, after telling the artist who has it, when someone else does. The
    artist's own lock from another workstation is offered for takeover --
    that is nearly always a session that crashed or was left behind.
    """
    info = lock.holder(path)
    force = False
    if info and info.get("user") == lock.me()["user"]:
        if not adapter.confirm(
                "You already have {0} open on {1} since {2}.\n\nIf that "
                "session is gone (crashed, or the machine was switched off), "
                "take the file over?".format(os.path.basename(path),
                                             info.get("host"),
                                             info.get("time"))):
            return False
        force = True
    elif info:
        adapter.message(
            "{0} is open by {1}.\n\nOnly one person can work in a file at a "
            "time. Ask them to close it, or open another version.".format(
                os.path.basename(path), lock.describe(info)))
        return False
    try:
        lock.take(path, force=force)
    except lock.Locked as e:
        adapter.message(str(e))
        return False
    except (OSError, IOError) as e:
        # A folder the lock cannot be written in must not stop the work.
        sys.stdout.write("Flow: could not lock {0}: {1}\n".format(path, e))
    return True


def opening(adapter, path):
    """Host callback for any open or save, Flow's or the host's own menu.

    Holds path and lets go of the previous file. False when someone else
    has it open, and the host then closes it again (or cancels the open).
    """
    if not path or not claim(adapter, path):
        return not path
    lock.keep_only(path)
    return True


def start(adapter):
    """At launch, on an untitled scene: open the artist's work, or begin it.

    An artist with files on the task gets their newest one opened as it is:
    no picker, no new version (Load File and Version Up stay on the menu).
    One with none gets their own empty v0001 saved into the work folder, so
    the first file is named and placed by the pipeline rather than by
    whoever saves first. Runs once per session, and never raises -- it is
    the first thing an artist sees.
    """
    if getattr(adapter, "_flow_started", False):
        return None
    adapter._flow_started = True
    ctx = context.get()
    try:
        current = adapter.current_scene()
        if current:                       # launched straight into a scene
            opening(adapter, current)
            return None
        if not ctx.has_task or not paths.work_dir(ctx):
            return None
        own = paths.artist_files(ctx)
        if own:
            return open_path(adapter, own[0]["path"])
        path = publish.save_next_version(adapter, ctx)
        sys.stdout.write("Flow: created {0}\n".format(path))
        return path
    except Exception as e:
        sys.stdout.write("Flow: startup skipped: {0}\n".format(e))
        return None


def discard_ok(adapter, path):
    """Ask before opening over unsaved changes. Defaults to Cancel."""
    return adapter.confirm(
        "The open scene has unsaved changes. Discard them and open\n"
        "{0}?".format(path))


def open_work_folder(adapter):
    return _open(adapter, paths.work_dir(), "work")


def open_publish_folder(adapter):
    return _open(adapter, paths.publish_dir(), "publish")


def show_context(adapter):
    """The Context panel: coordinates, then where the next save would land."""
    ctx = context.get()
    text = ctx.summary()
    try:
        text += "\n\nNext save   " + versioning.next_scene_path(ctx)
        text += "\nNext publish " + paths.publish_path(
            versioning.next_version(ctx), ctx)
    except ValueError:
        text += "\n\nNo task selected, so there is nowhere to save or publish."

    sys.stdout.write(text + "\n")
    adapter.message(text)
    return text


# -- helpers --------------------------------------------------------------

def _open(adapter, folder, label):
    if not folder:
        adapter.message(
            "This session was launched without a task, so there is no {0} "
            "folder. Pick a task in Flow and relaunch.".format(label))
        return None
    if not os.path.isdir(folder):
        adapter.message("The {0} folder does not exist yet:\n{1}".format(
            label, folder))
        return None

    opener = os.environ.get("FLOW_FILE_MANAGER", "xdg-open")
    try:
        subprocess.Popen([opener, folder], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as e:
        # start_new_session is POSIX-only; the farm is Linux, but a developer
        # box should get the path rather than a traceback.
        adapter.message("Could not open {0}: {1}".format(folder, e))
        return None
    return folder


def _under(path, folder):
    if not folder:
        return False
    return os.path.normpath(path).startswith(os.path.normpath(folder) + os.sep)


# -- wiring ---------------------------------------------------------------

# Menu attribute -> the function above it. ACTIONS names these; adapters get
# them from bind() rather than writing seven one-line wrappers each.
ACTION_FUNCS = {
    "action_load_file": load_file,
    "action_save": save,
    "action_save_as": save_as,
    "action_version_up": version_up,
    "action_publish": publish_scene,
    "action_submit": submit_to_deadline,
    "action_open_work_folder": open_work_folder,
    "action_open_publish_folder": open_publish_folder,
    "action_context": show_context,
}


def bind(module):
    """Give an adapter module its action_* entry points.

    An action the adapter already defines is left alone: a host whose file
    dialog cannot answer synchronously (Blender) has to own its own Save As,
    and overwriting it here would silently undo that.
    """
    funcs = dict(ACTION_FUNCS)
    if hasattr(module, "export_usd"):
        funcs.update({
            "action_publish_usd": usd.action_publish_usd,
            "action_load_usd_stage": usd.action_load_usd_stage,
            "action_open_usd_folder": usd.action_open_usd_folder,
        })
    for name, func in funcs.items():
        if not hasattr(module, name):
            setattr(module, name, functools.partial(func, module))
    return module
