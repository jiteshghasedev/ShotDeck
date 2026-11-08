"""Where this task's scenes live, from inside the DCC.

The entity root (/jobs/UAT6/sequences/SEQ001/shots/AD1030) is not re-derived
here: Flow already built it from config.ENTITY_PATH_TEMPLATES and exports
it as FLOW_ENTITY_ROOT. Duplicating the templates in a package that ships
separately is how the two drift apart.

Only the leaf -- which subfolder and which file extension a given DCC uses --
is decided here, and it matches config.ENTITY_SUBFOLDERS in the app.
"""

import os
import re

from . import context

# FLOW_SOFTWARE carries the rez package name, which is not always the
# adapter's -- or the folder's -- name. Normalising here rather than in
# __init__ keeps one spelling of "which DCC is this" for both the layouts
# below and the adapter lookup, which is what stops the two drifting apart.
ALIASES = {
    "sfx": "silhouette",
    "silhouettefx": "silhouette",
    "maya2026": "maya",
    "nukex": "nuke",
    "nukestudio": "nuke",
    "hou": "houdini",
    "houdinifx": "houdini",
    "3de": "tde4",
    "3de4": "tde4",
    "3dequalizer": "tde4",
    "3dequalizer4": "tde4",
    "painter": "substance",
    "substancepainter": "substance",
    "substance_painter": "substance",
    "substance3dpainter": "substance",
    "ae": "aftereffects",
    "afterfx": "aftereffects",
    "aftereffects": "aftereffects",
    "ps": "photoshop",
    "zb": "zbrush",
}


def normalise(software):
    """Adapter/layout name for a Flow software code. "" when unknown."""
    code = (software or "").strip().lower().replace(" ", "")
    return ALIASES.get(code, code)


# software name -> (subfolder under the entity root, scene extension).
# {step} is the pipeline step lowercased; it collapses out when the launch had
# no step, so a task without one still saves somewhere sensible.
#
# Hosts with no in-process Python (After Effects, Photoshop, ZBrush) are listed
# too: they have no adapter and never will, but Flow itself resolves work
# and publish folders through this table, so leaving them out would mean the
# app browsing the wrong folder for a real task.
LAYOUT = {
    "maya": ("maya/scenes/{step}", ".ma"),
    "nuke": ("nuke/{step}/scene", ".nk"),
    "silhouette": ("silhouette/{step}/scene", ".sfx"),
    "houdini": ("houdini/hip/{step}", ".hip"),
    "blender": ("blender/scenes/{step}", ".blend"),
    "tde4": ("3DE/{step}/scene", ".3de"),
    "substance": ("substance/{step}/scene", ".spp"),
    "rhino": ("rhino/{step}/scene", ".3dm"),
    "aftereffects": ("ae/{step}/scene", ".aep"),
    "photoshop": ("photoshop/{step}/scene", ".psd"),
    "zbrush": ("zbrush/{step}/scene", ".zpr"),
}
DEFAULT_LAYOUT = ("{software}/{step}/scene", "")

# Where a published scene lands, per DCC. Publishes sit beside the work
# folder rather than inside it, so "everything under publish/ is safe to
# reference" stays true no matter what an artist saves next door.
PUBLISH_LAYOUT = {
    "maya": "maya/publish/{step}",
    "nuke": "nuke/{step}/publish",
    "silhouette": "silhouette/{step}/publish",
    "houdini": "houdini/publish/{step}",
    "blender": "blender/publish/{step}",
    "tde4": "3DE/{step}/publish",
    "substance": "substance/{step}/publish",
    "rhino": "rhino/{step}/publish",
    "aftereffects": "ae/{step}/publish",
    "photoshop": "photoshop/{step}/publish",
    "zbrush": "zbrush/{step}/publish",
}
DEFAULT_PUBLISH_LAYOUT = "{software}/{step}/publish"

VERSION_RE = re.compile(r"_v(\d+)", re.IGNORECASE)

# uat6_SEQ001_AD1030_lighting_jitesh_v0001.ma
VERSION_PADDING = 4


def _layout(software):
    return LAYOUT.get(normalise(software), DEFAULT_LAYOUT)


def work_dir(ctx=None):
    """Folder this DCC's scenes belong in. None when the launch had no entity.

    None is a real answer -- Flow allows launching with no task selected,
    and the caller has to say "pick a task and relaunch" rather than invent a
    path in the artist's home directory.
    """
    ctx = ctx or context.get()
    root = (ctx.entity_root or "").rstrip("/")
    if not root:
        return None

    template, _ = _layout(ctx.software)
    relative = template.format(step=(ctx.step or "").lower(),
                               software=normalise(ctx.software) or "dcc")
    # Collapse the empty {step} rather than leaving a // in the path.
    parts = [p for p in relative.split("/") if p]
    return "/".join([root] + parts)


def publish_dir(ctx=None):
    """Folder published scenes belong in. None when the launch had no entity."""
    ctx = ctx or context.get()
    root = (ctx.entity_root or "").rstrip("/")
    if not root:
        return None

    template = PUBLISH_LAYOUT.get(normalise(ctx.software),
                                  DEFAULT_PUBLISH_LAYOUT)
    relative = template.format(step=(ctx.step or "").lower(),
                               software=normalise(ctx.software) or "dcc")
    parts = [p for p in relative.split("/") if p]
    return "/".join([root] + parts)


def publish_path(version, ctx=None, ext=None):
    """Where a version would publish to. Preview only -- see publish_for().

    The real publish takes its name from the scene the DCC actually saved, so
    this is what the menu shows before the artist agrees, not what gets used.
    """
    folder = publish_dir(ctx)
    if not folder:
        return None
    return folder + "/" + scene_name(version, ctx, ext)


def publish_for(work_path, ctx=None):
    """The publish path for a work file that exists, keeping its exact name.

    Publishing must never invent a second file name: the work file and its
    publish are the same bytes under the same name in a different folder, so
    they can be matched up by eye a year later.
    """
    folder = publish_dir(ctx)
    if not folder:
        return None
    return folder + "/" + os.path.basename(work_path)


def extension(ctx=None):
    ctx = ctx or context.get()
    _, ext = _layout(ctx.software)
    return ext


def scene_stem(ctx=None):
    """`uat6_SEQ001_AD1030_lighting_jitesh` -- everything but the version.

    Every component comes from the launch context; nothing here is entered by
    an artist or inferred from the open scene. Case follows the source: the
    project code and step are lowercased because that is how they appear in
    the paths, while the sequence and shot keep ShotGrid's own casing.

    A component the launch did not carry is dropped rather than filled with a
    placeholder -- a name with a gap is still traceable, a name with "None" in
    it is not.
    """
    ctx = ctx or context.get()
    bits = _task_bits(ctx) + [ctx.user or ""]
    return "_".join(b for b in bits if b) or "untitled"


def task_prefix(ctx=None):
    """`uat6_SEQ001_AD1030_lighting` -- the stem without the artist, shared
    by every artist's files on this task."""
    ctx = ctx or context.get()
    return "_".join(b for b in _task_bits(ctx) if b) or "untitled"


def _task_bits(ctx):
    return [
        (ctx.project_code or "").lower(),
        ctx.sequence or "",
        ctx.entity_name or "",
        (ctx.step or "").lower(),
    ]


def scene_name(version, ctx=None, ext=None):
    """`uat6_SEQ001_AD1030_lighting_jitesh_v0001.ma`."""
    ctx = ctx or context.get()
    if ext is None:
        ext = extension(ctx)
    return "{0}_v{1:0{2}d}{3}".format(scene_stem(ctx), int(version),
                                      VERSION_PADDING, ext)


def scene_path(version, ctx=None, ext=None):
    """Absolute path for a version, or None when there is no work folder."""
    folder = work_dir(ctx)
    if not folder:
        return None
    return folder + "/" + scene_name(version, ctx, ext)


def existing_versions(ctx=None, ext=None):
    """This artist's version numbers on disk for this task, ascending.

    Counted per artist, so every artist's own files start at v0001; the
    artist's name in the file name keeps work and publish paths apart.
    """
    return sorted(set(f["version"] for f in artist_files(ctx, ext)))


def artist_files(ctx=None, ext=None):
    """This artist's own work files for this task, newest version first."""
    ctx = ctx or context.get()
    me = (ctx.user or "").lower()
    return [f for f in task_files(ctx, ext) if (f["user"] or "").lower() == me]


def task_files(ctx=None, ext=None):
    """Every artist's work files for this task, newest version first.

    Each is {"path", "name", "version", "user", "mtime"}. Matches on the task
    prefix so an unrelated scene in a shared folder is not counted. The user
    is the artist the file is named after, which is who saved it through Flow;
    a file without one falls back to its owner on disk.
    """
    folder = work_dir(ctx)
    if not folder or not os.path.isdir(folder):
        return []

    ctx = ctx or context.get()
    if ext is None:
        ext = extension(ctx)
    pattern = re.compile(
        r"^{0}(?:_(?P<user>.+?))?_v(?P<version>\d+){1}$".format(
            re.escape(task_prefix(ctx)),
            re.escape(ext) if ext else r"(?:\.[^.]+)?"),
        re.IGNORECASE)

    found = []
    for name in os.listdir(folder):
        match = pattern.match(name)
        if not match:
            continue
        path = folder + "/" + name
        found.append({"path": path, "name": name,
                      "version": int(match.group("version")),
                      "user": match.group("user") or _owner(path),
                      "mtime": os.path.getmtime(path)})
    found.sort(key=lambda f: (f["version"], f["mtime"]), reverse=True)
    return found


def _owner(path):
    try:
        import pwd                  # POSIX only; Windows gets no owner
        return pwd.getpwuid(os.stat(path).st_uid).pw_name
    except Exception:
        return ""
