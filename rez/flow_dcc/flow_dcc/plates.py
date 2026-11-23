"""The shot's plate and render folder, from inside a DCC.

Both sit under FLOW_ENTITY_ROOT, in the folders config.ENTITY_SUBFOLDERS
names in the app: elements/plates and nuke/renders. No host module is
imported here, so this can be tested outside Nuke.
"""

import os
import re
import shutil

from . import context

PLATES = "elements/plates"
RENDERS = "nuke/renders"
# Departments whose renders go elsewhere than nuke/renders (lead feedback).
DEPARTMENT_RENDERS = {"prep": "elements/prep", "roto": "nuke/roto/renders"}
WORKFILE = "workfile"
FOR_APPROVAL = "for_approval"
# Publish copies a department's <name>/ render folder here (lead feedback).
PUBLISH_COPIES = {"roto": "elements/roto"}


def publish_copy(render_folder, ctx=None):
    """Copy <renders>/<name>/ (workfile/ and for_approval/) to the
    department's publish folder, e.g. elements/roto/<name>/. Returns the new
    folder, or None for a department that publishes in place."""
    ctx = ctx or context.get()
    target = PUBLISH_COPIES.get((ctx.department or "").lower())
    if not target or not ctx.entity_root:
        return None
    destination = "{0}/{1}/{2}".format(ctx.entity_root, target,
                                       os.path.basename(render_folder))
    if os.path.exists(destination):
        raise ValueError("Already published:\n" + destination)
    shutil.copytree(render_folder, destination)
    return destination

IMAGES = (".exr", ".dpx", ".tif", ".tiff", ".png", ".jpg", ".jpeg")
MOVIES = (".mov", ".mp4", ".mxf")

# plate_v001.1001.exr -> head "plate_v001.", frame "1001", ext ".exr"
FRAME_RE = re.compile(r"^(?P<head>.*?)(?P<frame>\d+)(?P<ext>\.[^.]+)$")


def plates_dir(ctx=None):
    ctx = ctx or context.get()
    return ctx.entity_root + "/" + PLATES if ctx.entity_root else None


def renders_dir(ctx=None):
    """This department's render folder: nuke/renders, or elements/prep for
    prep and nuke/roto/renders for roto."""
    ctx = ctx or context.get()
    if not ctx.entity_root:
        return None
    department = (getattr(ctx, "department", "") or "").lower()
    return ctx.entity_root + "/" + DEPARTMENT_RENDERS.get(department, RENDERS)


def render_roots(ctx=None):
    """Every department's render folder on this shot, for re-pointing a
    Write saved under another department's (or an older) layout."""
    ctx = ctx or context.get()
    if not ctx.entity_root:
        return []
    return [ctx.entity_root + "/" + r
            for r in [RENDERS] + list(DEPARTMENT_RENDERS.values())]


def find_plate(root):
    """The newest plate under root: {"path", "first", "last"}, or None.

    path is Nuke-style (plate.%04d.exr) for a sequence. A sequence beats a
    movie, and among sequences the format ranks before age, in IMAGES order:
    EXR frames beat a newer review JPEG of the same shot. A movie is the plate
    only when there is no sequence, and then first and last are None.

    shortcut: the newest sequence of the best format wins; offer a pick if
    shots start carrying several plates (BG01, FG01) that comps all need.
    """
    found = _scan(root)
    sequences = [p for p in found if p["first"] is not None]
    if not sequences:
        return _newest(found)
    best = min(_rank(p) for p in sequences)
    return _newest([p for p in sequences if _rank(p) == best])


def _rank(plate):
    return IMAGES.index(os.path.splitext(plate["path"])[1].lower())


def find_movie(root):
    """The newest movie under root (the plate's reference .mov), or None."""
    return _newest([p for p in _scan(root) if p["first"] is None])


def _newest(found):
    if not found:
        return None
    newest = max(found, key=lambda p: p["mtime"])
    return dict((k, newest[k]) for k in ("path", "first", "last"))


def _scan(root):
    """Every sequence and movie under root, each with its newest mtime."""
    if not root or not os.path.isdir(root):
        return []
    found = {}           # (dir, head, ext, padding) or movie path -> plate
    for folder, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(folder, name)
            lower = name.lower()
            if lower.endswith(MOVIES):
                found[full] = {"path": full.replace("\\", "/"), "first": None,
                               "last": None, "mtime": os.path.getmtime(full)}
                continue
            match = FRAME_RE.match(name)
            if not match or not lower.endswith(IMAGES):
                continue
            digits = match.group("frame")
            padding = len(digits) if digits.startswith("0") or \
                len(digits) >= 4 else 0
            key = (folder, match.group("head"), match.group("ext"), padding)
            frame = int(digits)
            plate = found.get(key)
            if plate is None:
                pattern = "%0{0}d".format(padding) if padding else "%d"
                plate = found[key] = {
                    "path": "{0}/{1}{2}{3}".format(
                        folder.replace("\\", "/"), match.group("head"),
                        pattern, match.group("ext")),
                    "first": frame, "last": frame, "mtime": 0}
            plate["first"] = min(plate["first"], frame)
            plate["last"] = max(plate["last"], frame)
            plate["mtime"] = max(plate["mtime"], os.path.getmtime(full))
    return list(found.values())


def write_path(ctx=None, script=None):
    """Nuke Write path under the render folder, named after the script:
    <renders>/<name>/for_approval/<name>/<name>.%04d.exr. The script itself
    is copied beside it, into <renders>/<name>/workfile/ (workfile_for).

    A plain path an artist can read; the Nuke adapter re-points it when the
    script is saved under a new version. An unsaved script has no name yet,
    so it gets the TCL expression that resolves to one at render time.
    """
    folder = renders_dir(ctx)
    if not folder:
        return None
    if script:
        name = os.path.splitext(os.path.basename(script))[0]
    else:
        name = "[file rootname [file tail [value root.name]]]"
    return "{0}/{1}/{2}/{1}/{1}.%04d.exr".format(folder, name, FOR_APPROVAL)


SPLINES = "nuke/roto/splines"
_PADDING_RE = re.compile(r"%0?(\d*)d")


def bracket_range(path, first, last):
    """plate.%04d.exr -> plate.[1001-1050].exr, the form Silhouette's
    fx.Source reads a sequence in. A movie (first None) is left alone."""
    if first is None:
        return path
    return _PADDING_RE.sub(lambda m: "[{0:0{2}d}-{1:0{2}d}]".format(
        first, last, int(m.group(1) or 0)), path, count=1)


def splines_path(ctx, scene, ext):
    """nuke/roto/splines/<scene name><ext> (lead feedback 11.5)."""
    name = os.path.splitext(os.path.basename(scene.rstrip("/")))[0]
    return "{0}/{1}/{2}{3}".format(ctx.entity_root, SPLINES, name, ext)


def copy_scene(scene, target):
    """Copy a scene to target: a file (.nk), or a folder (a Silhouette .sfx
    project is a directory). An older copy at target is replaced."""
    folder = os.path.dirname(target)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    if os.path.isdir(scene):
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(scene, target)
    else:
        shutil.copy2(scene, target)
    return target


def workfile_for(frames, script):
    """Where script is copied when frames render, or None when frames are
    not in the for_approval layout: .../<name>/workfile/<script name>."""
    frames = frames.replace("\\", "/")
    approval = os.path.dirname(os.path.dirname(frames))
    if os.path.basename(approval) != FOR_APPROVAL:
        return None
    return "{0}/{1}/{2}".format(os.path.dirname(approval), WORKFILE,
                                os.path.basename(script))
