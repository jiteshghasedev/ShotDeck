"""The shot's plate and render folder, from inside a DCC.

Both sit under FLOW_ENTITY_ROOT, in the folders config.ENTITY_SUBFOLDERS
names in the app: elements/plates and nuke/renders. No host module is
imported here, so this can be tested outside Nuke.
"""

import os
import re

from . import context

PLATES = "elements/plates"
RENDERS = "nuke/renders"

IMAGES = (".exr", ".dpx", ".tif", ".tiff", ".png", ".jpg", ".jpeg")
MOVIES = (".mov", ".mp4", ".mxf")

# plate_v001.1001.exr -> head "plate_v001.", frame "1001", ext ".exr"
FRAME_RE = re.compile(r"^(?P<head>.*?)(?P<frame>\d+)(?P<ext>\.[^.]+)$")


def plates_dir(ctx=None):
    ctx = ctx or context.get()
    return ctx.entity_root + "/" + PLATES if ctx.entity_root else None


def renders_dir(ctx=None):
    ctx = ctx or context.get()
    return ctx.entity_root + "/" + RENDERS if ctx.entity_root else None


def find_plate(root):
    """The newest plate under root: {"path", "first", "last"}, or None.

    path is Nuke-style (plate.%04d.exr) for a sequence. A sequence beats a
    movie; a movie is the plate only when there is no sequence, and then
    first and last are None.

    shortcut: the newest sequence wins; offer a pick if shots start carrying
    several plates (BG01, FG01) that comps all need.
    """
    found = _scan(root)
    return _newest([p for p in found if p["first"] is not None] or found)


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
    """Nuke Write path under the render folder, named after the script.

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
    return "{0}/{1}/{1}.%04d.exr".format(folder, name)
