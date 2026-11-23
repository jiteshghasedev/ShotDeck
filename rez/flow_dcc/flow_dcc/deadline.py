"""Submitting the open scene to Deadline, from inside the DCC.

One submitter for every host, because Deadline's own interface is the same for
all of them: two plain text files -- a job info file and a plugin info file --
handed to `deadlinecommand`. Nothing here imports Deadline's Python API, so no
DCC needs Deadline's submitter scripts on its path and nothing has to be
rewritten when the farm upgrades.

What is genuinely per-host is small. It lives in PLUGINS below, plus two
optional hooks an adapter may implement:

    frame_range()             (first, last) for the open scene
    deadline_plugin_info()    plugin keys only that host knows -- Houdini's
                              ROP, Nuke's write node, Maya's project path

The submitted scene is the work file on /jobs, never a copy in the repository:
the farm mounts the same storage the artist does, and a job that renders a
snapshot nobody can find again is worse than no job.

Failure is loud and early. Everything here happens before a frame is rendered,
so there is nothing to half-finish -- either Deadline accepted the job and
returned an id, or the artist is told exactly what to fix.
"""

import os
import subprocess
import tempfile

from . import context, paths

# Normalised software name -> Deadline plugin. A host that is absent has no
# Deadline plugin to submit to, which is a different thing from a host with no
# adapter: 3DEqualizer, Substance Painter and Rhino do not render frames on a
# render farm, and pretending otherwise would queue jobs that cannot run.
PLUGINS = {
    "maya": "MayaBatch",
    "nuke": "Nuke",
    "houdini": "Houdini",
    "blender": "Blender",
    "aftereffects": "AfterEffects",
}

# Farm defaults. Set per project in the launch environment (envs/*.yml) rather
# than edited here -- a pool name is the studio's business, not this package's.
POOL = os.environ.get("FLOW_DEADLINE_POOL", "none")
GROUP = os.environ.get("FLOW_DEADLINE_GROUP", "none")
PRIORITY = os.environ.get("FLOW_DEADLINE_PRIORITY", "50")
CHUNK_SIZE = os.environ.get("FLOW_DEADLINE_CHUNK", "1")

# Where deadlinecommand is, when it is not on PATH.
COMMAND_VAR = "DEADLINE_COMMAND"
PATH_VAR = "DEADLINE_PATH"


class DeadlineError(RuntimeError):
    """A submission that did not happen. Nothing was queued."""


class Job(object):
    """What Deadline accepted."""

    def __init__(self, job_id, name, plugin, frames, scene):
        self.id = job_id
        self.name = name
        self.plugin = plugin
        self.frames = frames
        self.scene = scene

    def summary(self):
        return "\n".join([
            "Submitted to Deadline",
            "",
            "Job         {0}".format(self.name),
            "Job ID      {0}".format(self.id or "not reported"),
            "Plugin      {0}".format(self.plugin),
            "Frames      {0}".format(self.frames),
            "Scene       {0}".format(self.scene),
        ])


# -- what Deadline is asked to run ----------------------------------------

def executable():
    """Path to deadlinecommand, or None when this box has no Deadline client."""
    explicit = os.environ.get(COMMAND_VAR)
    if explicit and os.path.isfile(explicit):
        return explicit

    root = os.environ.get(PATH_VAR)
    if root:
        for name in ("deadlinecommand", "deadlinecommand.exe"):
            candidate = os.path.join(root, name)
            if os.path.isfile(candidate):
                return candidate

    # PATH is the last resort rather than the first: a farm that sets
    # DEADLINE_PATH means that Deadline, and a stray client on PATH is how a
    # job ends up submitted to the wrong repository.
    import shutil
    return shutil.which("deadlinecommand")


def plugin(ctx=None):
    """Deadline plugin for this session's DCC, or None."""
    ctx = ctx or context.get()
    return PLUGINS.get(paths.normalise(ctx.software))


def validate(adapter, ctx=None):
    """Everything that must be true before a job is written.

    Raises DeadlineError naming the one thing to fix, in the order an artist
    can act on it: is there a farm client, does this DCC render on the farm,
    is the scene saved where the farm can read it.
    """
    ctx = ctx or context.get()

    if executable() is None:
        raise DeadlineError(
            "No Deadline client on this machine: deadlinecommand is not on "
            "PATH and neither {0} nor {1} points at one.".format(
                COMMAND_VAR, PATH_VAR))

    name = plugin(ctx)
    if name is None:
        raise DeadlineError(
            "Deadline has no plugin for {0}, so there is nothing to submit "
            "from here. Render it locally, or publish the scene and submit "
            "the render from a host that has one.".format(
                ctx.software or "this DCC"))

    scene = adapter.current_scene()
    if not scene or not os.path.isfile(scene):
        raise DeadlineError(
            "The open scene has not been saved, so the farm has no file to "
            "render. Use Version Up first.")

    work_dir = paths.work_root(ctx)       # any artist's roto folder too
    if work_dir and not os.path.normpath(scene).startswith(
            os.path.normpath(work_dir) + os.sep):
        raise DeadlineError(
            "This scene is outside the task's work folder:\n{0}\n\nThe farm "
            "renders what is on shared storage, so save it into\n{1}\nwith "
            "Version Up first.".format(scene, work_dir))

    return name, scene


def frames(adapter):
    """The scene's frame range as Deadline writes it, e.g. "1001-1120"."""
    read = getattr(adapter, "frame_range", None)
    span = read() if callable(read) else None
    if not span:
        raise DeadlineError(
            "Flow could not read a frame range from this scene, and will "
            "not guess one -- a wrong range is a wasted farm night. Set the "
            "scene's range and submit again.")

    first, last = span
    return "{0}-{1}".format(int(first), int(last))


def job_info(scene, plugin_name, frame_string, ctx=None, description=""):
    """The job info file, as key/value pairs.

    Carries the launch context twice on purpose: as ExtraInfoKeyValue, which a
    supervisor reads in the Monitor, and as EnvironmentKeyValue, so the render
    job resolves the same shot the artist did.
    """
    ctx = ctx or context.get()

    info = {
        "Plugin": plugin_name,
        "Name": os.path.basename(scene),
        "BatchName": "{0} / {1}".format(ctx.entity_name or "no entity",
                                        ctx.task_name or "no task"),
        "Comment": description or "Submitted from {0} by Flow".format(
            ctx.software or "a DCC"),
        "Department": ctx.step or "",
        "UserName": ctx.user or "",
        "Frames": frame_string,
        "ChunkSize": CHUNK_SIZE,
        "Priority": PRIORITY,
        "Pool": POOL,
        "Group": GROUP,
    }

    extras = [
        ("Project", ctx.project_name),
        ("ProjectId", ctx.project_id),
        ("Entity", ctx.entity_name),
        ("EntityId", ctx.entity_id),
        ("EntityType", ctx.entity_type),
        ("Task", ctx.task_name),
        ("TaskId", ctx.task_id),
        ("Step", ctx.step),
    ]
    index = 0
    for key, value in extras:
        if value in (None, ""):
            continue               # a gap is more honest than "None" on a job
        info["ExtraInfoKeyValue{0}".format(index)] = "{0}={1}".format(
            key, value)
        index += 1

    # The whole launch context, so the render is the same launch as the save.
    # SG_SCRIPT_KEY is deliberately not among these -- see publish.py. A job
    # info file is readable by anyone with the Monitor open.
    index = 0
    for key in sorted(os.environ):
        if key.startswith("FLOW_"):
            info["EnvironmentKeyValue{0}".format(index)] = "{0}={1}".format(
                key, os.environ[key])
            index += 1

    return info


def plugin_info(scene, adapter, ctx=None):
    """The plugin info file: the scene, plus whatever only the host knows."""
    ctx = ctx or context.get()
    info = {"SceneFile": scene}
    if ctx.software_version:
        # Deadline matches this against the versions it has installed, and
        # wants major.minor -- which is the front of what Flow launched.
        info["Version"] = ".".join(ctx.software_version.split(".")[:2])

    host = getattr(adapter, "deadline_plugin_info", None)
    if callable(host):
        info.update(host(scene) or {})
    return info


# -- submitting ------------------------------------------------------------

def submit(adapter, ctx=None, description=""):
    """Validate, write the two info files, run deadlinecommand. Returns a Job."""
    ctx = ctx or context.get()
    plugin_name, scene = validate(adapter, ctx)
    frame_string = frames(adapter)
    # The farm's Nuke has no Flow menu to copy the script beside its render.
    snapshot = getattr(adapter, "snapshot_workfiles", None)
    if snapshot:
        snapshot(scene)

    folder = tempfile.mkdtemp(prefix="flow-deadline-")
    job_file = _write(os.path.join(folder, "job_info.job"),
                      job_info(scene, plugin_name, frame_string, ctx,
                               description))
    plugin_file = _write(os.path.join(folder, "plugin_info.job"),
                         plugin_info(scene, adapter, ctx))

    output = run([executable(), job_file, plugin_file])
    return Job(_job_id(output), os.path.basename(scene), plugin_name,
               frame_string, scene)


def run(command):
    """Run deadlinecommand and return its output. Replaced in the tests."""
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
    except OSError as e:
        raise DeadlineError("Could not run {0}: {1}".format(command[0], e))
    except subprocess.TimeoutExpired:
        raise DeadlineError(
            "Deadline did not answer within two minutes. The repository may "
            "be unreachable from this machine.")

    output = (result.stdout or b"").decode("utf-8", "replace")
    if result.returncode != 0:
        raise DeadlineError(
            "Deadline rejected the job:\n\n{0}".format(output.strip()))
    return output


def _job_id(output):
    """The JobID line deadlinecommand prints, or None.

    A submission that reports no id is not a failure -- the job is queued
    either way -- so this reports what it found rather than raising.
    """
    for line in (output or "").splitlines():
        if line.strip().startswith("JobID="):
            return line.split("=", 1)[1].strip()
    return None


def _write(path, info):
    """One key=value per line, which is the whole of Deadline's format."""
    with open(path, "w") as handle:
        for key in sorted(info):
            handle.write("{0}={1}\n".format(key, info[key]))
    return path
