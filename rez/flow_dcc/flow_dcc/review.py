"""Sequence review: a lead's one Nuke script for a whole sequence.

Every shot the lead reviews (Task.task_reviewers, config.TASK_LEAD_FIELD in
the app) gets its plate, its movie and an annotation Write. The lead draws,
renders the frames they want, and the JPEGs land in the shot's

    elements/images/annotation/<dept>/<shot>_<artist>_<dept>_annot_<when>/

with the render's time in the name, so a second round never overwrites the
first. The frames then go up as a Note on the Shot. ShotGrid being down never
costs the render: the Note waits in a queue on this machine until the lead
picks Flow > Retry Pending Notes.

No host module is imported here, so all of it is tested outside Nuke.
"""

import datetime
import glob
import json
import os
import re
import uuid

from . import context, paths

LEAD_FIELD = os.environ.get("FLOW_TASK_LEAD_FIELD", "task_reviewers")
ANNOTATIONS = "elements/images/annotation"
QUEUE_DIR = os.environ.get(
    "FLOW_PENDING_NOTES", os.path.expanduser("~/.flow/pending_notes"))


# -- where things go ------------------------------------------------------

def shot_root(code, ctx=None):
    """A sibling shot's folder: the launch's shot folder with another name.

    shortcut: assumes every shot of the sequence sits in one folder
    (.../sequences/SEQ001/shots/<shot>), which ENTITY_PATH_TEMPLATES gives
    today; export the template to the DCC if a project ever splits them.
    """
    ctx = ctx or context.get()
    root = (ctx.entity_root or "").rstrip("/")
    return os.path.dirname(root) + "/" + code if root else None


def sequence_root(ctx=None):
    """/jobs/UAT6/sequences/SEQ001, or None when the shot is not under one."""
    ctx = ctx or context.get()
    shots = os.path.dirname((ctx.entity_root or "").rstrip("/"))
    if os.path.basename(shots) != "shots":
        return None
    return os.path.dirname(shots)


def script_path(ctx=None):
    """The lead's next review script: nuke/review/SEQ001_review_nanda_v0001.nk.

    The sequence is in this name, unlike a shot's: there is no shot to carry
    it.
    """
    ctx = ctx or context.get()
    root = sequence_root(ctx)
    if not root:
        return None
    folder = root + "/nuke/review"
    stem = "{0}_review_{1}".format(ctx.sequence or os.path.basename(root),
                                   ctx.artist or "lead")
    taken = [int(m.group(1)) for m in (
        re.search(r"_v(\d+)\.nk$", f) for f in
        glob.glob(os.path.join(folder, glob.escape(stem) + "_v*.nk"))) if m]
    return "{0}/{1}_v{2:0{3}d}.nk".format(folder, stem, max(taken or [0]) + 1,
                                          paths.VERSION_PADDING)


def annotation_name(shot, department, when=None, ctx=None):
    """AD1008_nanda_comp_annot_20261115_1430."""
    ctx = ctx or context.get()
    when = when or datetime.datetime.now()
    return "{0}_{1}_{2}_annot_{3:%Y%m%d_%H%M}".format(
        shot, ctx.artist or "lead", paths.department_code(department), when)


def annotation_path(shot, department, when=None, ctx=None):
    """The JPEG sequence one render of shot's annotations writes."""
    root = shot_root(shot, ctx)
    if not root:
        return None
    name = annotation_name(shot, department, when, ctx)
    return "{0}/{1}/{2}/{3}/{3}.%04d.jpg".format(
        root, ANNOTATIONS, paths.department_code(department), name)


NOTES_FOLDER = "Reference/Annotated_notes"
_FRAME_TAIL_RE = re.compile(r"(?:[._](?:%0?\d*d|#+|@+|\d+))?\.[^.]+$")


def project_root(ctx=None):
    """/jobs/3cbien_Test: the entity root up to its sequences/ or assets/."""
    ctx = ctx or context.get()
    root = (ctx.entity_root or "").rstrip("/")
    for marker in ("/sequences/", "/assets/"):
        if marker in root:
            return root.split(marker)[0]
    return None


def annotated_note_path(source, frame, ctx=None):
    """<project>/Reference/Annotated_notes/<source>_<frame>_v001.jpg, the
    next free version. source is a Read's file (its name without frame
    number and extension is used) or a node name."""
    root = project_root(ctx)
    if not root:
        return None
    stem = _FRAME_TAIL_RE.sub("", os.path.basename(source)) or source
    folder = root + "/" + NOTES_FOLDER
    head = "{0}_{1}".format(stem, int(frame))
    taken = [int(m.group(1)) for m in (
        re.search(r"_v(\d+)\.jpg$", f) for f in
        glob.glob(os.path.join(folder, glob.escape(head) + "_v*.jpg"))) if m]
    return "{0}/{1}_v{2:03d}.jpg".format(folder, head, max(taken or [0]) + 1)


# -- which shots ----------------------------------------------------------

def me(sg, ctx=None):
    """The lead's HumanUser, found by the email Flow exported."""
    email = os.environ.get("FLOW_USER_EMAIL", "")
    if not email:
        return None
    return sg.find_one("HumanUser", [["email", "is", email]], ["name"])


def shots(sg, ctx=None, everything=False):
    """[{"id", "code", "department", "root"}] of the launch shot's sequence.

    Only the shots the lead reviews a task on, unless everything is True.
    department is the reviewed task's step; a shot the lead reviews nothing
    on (everything=True) takes the lead's own department.
    """
    ctx = ctx or context.get()
    shot = sg.find_one("Shot", [["id", "is", ctx.entity_id]], ["sg_sequence"])
    sequence = (shot or {}).get("sg_sequence")
    if not sequence:
        raise ValueError("This shot is not in a sequence in ShotGrid.")
    rows = sg.find("Shot", [["sg_sequence", "is", sequence]], ["code"])
    lead = me(sg, ctx)
    reviewed = {}
    if lead and rows:
        for task in sg.find("Task", [
                ["entity", "in", [{"type": "Shot", "id": r["id"]}
                                  for r in rows]],
                [LEAD_FIELD, "is", {"type": "HumanUser", "id": lead["id"]}]],
                ["entity", "step"]):
            step = (task.get("step") or {}).get("name") or ""
            reviewed.setdefault(task["entity"]["id"], step.lower())
    out = []
    for row in sorted(rows, key=lambda r: r.get("code") or ""):
        if row["id"] not in reviewed and not everything:
            continue
        out.append({"id": row["id"], "code": row["code"],
                    "department": reviewed.get(row["id"]) or ctx.department,
                    "root": shot_root(row["code"], ctx)})
    return out


# -- the Note, now or later -----------------------------------------------

def note_entry(shot, department, images, text="", ctx=None):
    """What one rendered round of annotations posts, as plain JSON."""
    ctx = ctx or context.get()
    name = ctx.artist[:1].upper() + ctx.artist[1:] if ctx.artist else "Lead"
    return {
        "project": {"type": "Project", "id": ctx.project_id},
        "shot": {"type": "Shot", "id": shot["id"], "name": shot["code"]},
        "subject": "{0} {1} annotations by {2}".format(
            shot["code"], paths.department_code(department), name),
        "content": "{0}: {1}".format(
            name, text or "Annotations, {0} frame(s): {1}".format(
                len(images), os.path.dirname(images[0]) if images else "")),
        "images": list(images),
        "note_id": None,
    }


def post(sg, entry):
    """Create the Note (once) and attach every image not yet attached.

    The entry remembers the Note and what went up, so a retry after a
    failure part-way never makes a second Note or a second copy of a frame.
    """
    if not entry.get("note_id"):
        note = sg.create("Note", {"project": entry["project"],
                                  "note_links": [{"type": "Shot",
                                                  "id": entry["shot"]["id"]}],
                                  "subject": entry["subject"],
                                  "content": entry["content"]})
        entry["note_id"] = note["id"]
    while entry["images"]:
        image = entry["images"][0]
        if os.path.isfile(image):
            sg.upload("Note", entry["note_id"], image)
        entry["images"].pop(0)
    return entry["note_id"]


def send_or_queue(sg_factory, entry):
    """Post now; on any failure keep the entry for Retry Pending Notes.

    Returns (posted, reason). The images are already on disk either way.
    """
    try:
        post(sg_factory(), entry)
        return True, ""
    except Exception as e:
        queue(entry)
        return False, str(e)


def queue(entry):
    if not os.path.isdir(QUEUE_DIR):
        os.makedirs(QUEUE_DIR)
    path = entry.get("_file") or os.path.join(
        QUEUE_DIR, uuid.uuid4().hex + ".json")
    entry["_file"] = path
    with open(path, "w") as f:
        json.dump(entry, f, indent=2)
    return path


def pending():
    found = []
    for path in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json"))):
        try:
            with open(path) as f:
                entry = json.load(f)
        except (ValueError, OSError, IOError):
            continue
        entry["_file"] = path
        found.append(entry)
    return found


def retry(sg_factory):
    """Post every queued Note. Returns (sent, still_pending, last_error)."""
    entries = pending()
    if not entries:
        return 0, 0, ""
    try:
        sg = sg_factory()
    except Exception as e:
        return 0, len(entries), str(e)
    sent, error = 0, ""
    for entry in entries:
        try:
            post(sg, entry)
        except Exception as e:
            queue(entry)                # keeps the Note id and what is left
            error = str(e)
            continue
        os.remove(entry["_file"])
        sent += 1
    return sent, len(entries) - sent, error
