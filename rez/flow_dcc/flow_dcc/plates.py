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

    path is Nuke-style (plate.%04d.exr) for a sequence; first and last are
    None for a movie.

    shortcut: the newest sequence wins; offer a pick if shots start carrying
    several plates (BG01, FG01) that comps all need.
    """
    if not root or not os.path.isdir(root):
        return None
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
    if not found:
        return None
    newest = max(found.values(), key=lambda p: p["mtime"])
    return dict((k, newest[k]) for k in ("path", "first", "last"))


def write_path(ctx=None):
    """Nuke Write path under the render folder, named after the script.

    A TCL expression rather than a fixed version, so Version Up renders into
    a new folder without anyone touching the Write node.
    """
    folder = renders_dir(ctx)
    if not folder:
        return None
    name = "[file rootname [file tail [value root.name]]]"
    return "{0}/{1}/{1}.%04d.exr".format(folder, name)
