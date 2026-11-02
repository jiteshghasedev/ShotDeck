"""Publishing a scene from inside the DCC.

The pipeline, in order:

    read context -> validate -> determine workfile -> determine version
      -> determine publish destination -> save/version the scene
      -> create the publish -> validate the published file
      -> register in ShotGrid -> report

Two rules shape the error handling. Everything before the save is a *check* --
it fails cheaply, changes nothing, and says why. Everything from the save
onward has already touched the disk, so a later failure reports what was
written rather than pretending the publish did not happen: a scene published
on disk but unregistered in ShotGrid is a bookkeeping problem, and telling the
artist their publish failed would be a lie that costs them the work.

Scope note: this publishes the *scene file*. Media publishing -- preflight,
media inspection, upload, notes -- stays in Flow's publish dialog, which
already owns it.

Security note: registration authenticates with SG_SCRIPT_KEY, which reaches
the DCC only because launcher.py builds the launch environment from
os.environ. It is deliberately not part of the FLOW_* context. Anything
running in the DCC can read it -- worth knowing before widening what the key
is allowed to do.
"""

import os
import shutil
import sys

from . import context, lock, paths, shotgrid, versioning


class PublishError(RuntimeError):
    """A publish that did not happen. Nothing was written."""


class Result(object):
    """What a publish produced, and what it could not finish."""

    def __init__(self, work_path, publish_path, version, published_file=None,
                 registration_error=None):
        self.work_path = work_path
        self.publish_path = publish_path
        self.version = version
        self.published_file = published_file
        self.registration_error = registration_error

    @property
    def registered(self):
        return self.published_file is not None

    def summary(self):
        lines = [
            "Published v{0:03d}".format(self.version),
            "",
            "Work file   {0}".format(self.work_path),
            "Publish     {0}".format(self.publish_path),
        ]
        if self.registered:
            lines.append("ShotGrid    PublishedFile {0}".format(
                self.published_file.get("id")))
        else:
            lines.append("ShotGrid    NOT registered")
            lines.append("")
            lines.append(str(self.registration_error))
        return "\n".join(lines)


# -- stages ---------------------------------------------------------------

def validate(ctx=None):
    """Everything that must be true before anything is written.

    Raises PublishError naming the one thing to fix. Checked in the order an
    artist can act on: context first, then disk.
    """
    ctx = ctx or context.get()

    missing = ctx.missing()
    if missing:
        raise PublishError(
            "This session was not launched from Flow ({0} not set), so "
            "there is no task to publish to.".format(", ".join(missing)))
    if not ctx.has_task:
        raise PublishError(
            "This session was launched without a task selected. Pick a task "
            "in Flow and relaunch before publishing.")

    work_dir = paths.work_dir(ctx)
    publish_dir = paths.publish_dir(ctx)
    if not work_dir or not publish_dir:
        raise PublishError(
            "Flow did not resolve a folder for {0}. The shot may not be "
            "built out on disk yet.".format(ctx.entity_name or "this task"))

    # The entity root has to exist; the leaf folders are created on demand.
    root = (ctx.entity_root or "").rstrip("/")
    if not os.path.isdir(root):
        raise PublishError(
            "The folder for this task does not exist:\n{0}".format(root))

    return ctx


def prepare(ctx=None):
    """Work out what a publish would write, without writing it.

    Returns (work_path, publish_path, version). Used by the publish action and
    by the menu label, so the artist can see the destination before agreeing.
    """
    ctx = validate(ctx)
    version = versioning.next_version(ctx)
    work_path = paths.scene_path(version, ctx)
    publish_path = paths.publish_path(version, ctx)

    if os.path.exists(publish_path):
        # The version scan reads the work folder, so a publish folder that has
        # run ahead of it means someone published out of band.
        raise PublishError(
            "A publish already exists at this version:\n{0}\n\nSomeone may "
            "have published outside Flow. Check with them before "
            "overwriting.".format(publish_path))

    return work_path, publish_path, version


def publish(adapter, ctx=None, description=""):
    """Run the whole pipeline. Returns a Result.

    The publish name is taken from the scene the DCC actually saved, not from
    the name we asked it to save under -- a host that redirects the save (a
    workspace rule, a file-type change, an artist's own Save As mid-flight)
    would otherwise publish a name that does not exist on disk.

    Raises PublishError only for failures *before* anything is written. Once
    the scene is saved, problems are reported on the Result instead.
    """
    ctx = ctx or context.get()
    target, _, _ = prepare(ctx)

    # -- save the scene first
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        os.makedirs(folder)

    try:
        adapter.save_scene(target)
    except Exception as e:
        raise PublishError("Could not save the scene to\n{0}\n\n{1}".format(
            target, e))

    # -- get the actual saved filename, and the version written in it
    work_path = adapter.current_scene() or target
    if not os.path.isfile(work_path):
        raise PublishError(
            "{0} reported a successful save but there is no file at\n{1}"
            .format(ctx.software or "The DCC", work_path))
    hold(work_path)

    version = versioning.version_in(work_path)
    if version is None:
        raise PublishError(
            "The saved scene has no version in its name, so Flow cannot "
            "publish it:\n{0}\n\nUse Version Up to save a versioned scene "
            "first.".format(work_path))

    # -- create the publish directory, publish under the same name
    publish_path = paths.publish_for(work_path, ctx)
    publish_folder = os.path.dirname(publish_path)
    if not os.path.isdir(publish_folder):
        os.makedirs(publish_folder)

    shutil.copy2(work_path, publish_path)

    # -- validate the published file
    _verify_copy(work_path, publish_path)

    # -- register in ShotGrid
    published_file = None
    registration_error = None
    try:
        published_file = shotgrid.register(publish_path, ctx, description)
    except shotgrid.NotConfigured as e:
        registration_error = e
    except Exception as e:
        registration_error = RuntimeError(
            "ShotGrid rejected the publish record: {0}\n\nThe file is "
            "published on disk. Register it from Flow, or ask a "
            "supervisor to.".format(e))

    return Result(work_path, publish_path, version, published_file,
                  registration_error)


def _verify_copy(source, destination):
    """The published file is really there, and really the same file.

    Size rather than a hash: a scene can be gigabytes, an artist is waiting,
    and the failure this catches is a truncated copy onto a full or dropped
    mount -- which size sees.
    """
    if not os.path.isfile(destination):
        raise PublishError(
            "The publish did not appear at\n{0}".format(destination))

    written, expected = os.path.getsize(destination), os.path.getsize(source)
    if written != expected:
        raise PublishError(
            "The published file is {0} bytes but the work file is {1}. The "
            "copy was truncated -- check free space on the publish "
            "mount.\n\n{2}".format(written, expected, destination))


# -- helpers used by the menu ---------------------------------------------

def save_next_version(adapter, ctx=None):
    """Save the open scene as the next work version. Returns the path."""
    ctx = ctx or context.get()
    path = versioning.next_scene_path(ctx, create_dir=True)
    adapter.save_scene(path)
    hold(path)
    return path


def hold(path):
    """This session now works in path, a file Flow just saved: lock it.

    The file is new, so nobody else can have it; a lock that cannot be
    written is never worth failing a save that already happened.
    """
    try:
        lock.take(path)
        lock.keep_only(path)
    except Exception as e:
        sys.stdout.write("Flow: could not lock {0}: {1}\n".format(path, e))


def describe_target(ctx=None):
    """Where the next save would go, as text for a menu or a dialog."""
    ctx = ctx or context.get()
    try:
        return versioning.next_scene_path(ctx)
    except ValueError as e:
        return str(e)
