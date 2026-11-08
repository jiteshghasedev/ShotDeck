"""Review annotations from inside OpenRV: draw on frames, send them to ShotGrid.

OpenRV ships without the ShotGrid integration commercial RV has, so a
reviewer's annotations would otherwise never leave RV. rv_player starts RV
with a -pyeval that puts this folder on sys.path and calls install(); the menu
it adds is

    Flow > Send Annotations to ShotGrid...

which finds every frame with a drawing on it (or takes the frame on screen
when nothing is drawn), asks for an optional note, and creates one Note per
Version with those frames attached:

    subject     AD1030_v002 annotations by Priya, frames 1012, 1020
    content     Priya: Edge flickers here
    attachments AD1030_v002_annotation_f1012_priya.png, ..._f1020_priya.png

linked to the Version, its shot and its task. The Note is written by the
script user like every note Flow writes; the reviewer is the "Priya: "
signature, which Flow and FlowDash read back as the author.

What RV does not know, rv_player tells it in FLOW_RV_REVIEW (JSON): which
Version each source path is, who is reviewing, and the site. Only install()
and the RV helpers below touch RV; the rest is plain Python, tested without it.
"""

import json
import os
import re
import tempfile

REVIEW_VAR = "FLOW_RV_REVIEW"
SCRIPT_DEFAULT = "SG_daemon"

# The frame part of a sequence path and everything after it: RV may hand back
# file.1001-1100#.exr for the file.%04d.exr it was given.
_FRAMES = re.compile(r"[.\-_]?(%0?\d*d|\d*-?\d*[#@]+).*$")

_mode = None        # the live MinorMode; kept so it is never collected


def payload(environ=None):
    """The review context rv_player exported, or {} outside a Flow launch."""
    try:
        return json.loads((environ or os.environ).get(REVIEW_VAR) or "{}")
    except ValueError:
        return {}


def _stem(path):
    path = os.path.normpath(path or "")
    return os.path.join(os.path.dirname(path),
                        _FRAMES.sub("", os.path.basename(path)))


def version_for(media, versions):
    """The Version a source came from: same path, same sequence, or the only one."""
    for v in versions:
        if os.path.normpath(v["path"]) == os.path.normpath(media or ""):
            return v
    for v in versions:
        if _stem(v["path"]) == _stem(media):
            return v
    return versions[0] if len(versions) == 1 else None


def signer_name(login):
    """jitesh -> Jitesh, as notes_service.signer_name in the app."""
    return login[:1].upper() + login[1:] if login else ""


def sign(content, login):
    """Same as notes_service.sign in the app: "Jitesh: " in front, once."""
    if not login:
        return content
    name = signer_name(login)
    if content.lower().startswith(name.lower() + ":"):
        return content
    return "%s: %s" % (name, content)


def _safe(text):
    """A file-name-safe form: AD1030 v002/comp -> AD1030_v002_comp."""
    return re.sub(r"[^\w.\-]+", "_", text or "").strip("_")


def attachment_name(version, frame, login):
    """AD1030_v002_annotation_f1012_priya.png: version, frame and who drew it."""
    code = _safe(version.get("code")) or "Version%s" % version["id"]
    return "%s_annotation_f%04d_%s.png" % (code, frame, _safe(login) or "unknown")


def _frames_label(frames):
    return "%s %s" % ("frame" if len(frames) == 1 else "frames",
                      ", ".join(str(f) for f in frames))


def parse_frames(text):
    """The frame numbers in Mu's printout of an int[]: "int[] {1, 12}" -> [1, 12]."""
    text = text or ""
    if "{" in text:
        text = text[text.index("{"):]
    return sorted({int(n) for n in re.findall(r"-?\d+", text)})


def note_data(review, version, text, frames):
    """The Note for this Version, shaped like SGClient.create_note's."""
    links = [{"type": "Version", "id": version["id"]}]
    entity = version.get("entity")
    if entity:
        links.append({"type": entity["type"], "id": entity["id"]})
    login = review.get("login") or ""
    by = " by %s" % signer_name(login) if login else ""
    label = _frames_label(frames)
    data = {
        "project": {"type": "Project", "id": review["project"]["id"]},
        "subject": "%s annotations%s, %s" % (version.get("code") or "", by, label),
        "content": sign(text or "Annotations on %s" % label, login),
        "note_links": links,
    }
    task = version.get("task")
    if task:
        data["tasks"] = [{"type": "Task", "id": task["id"]}]
    # No "user": the Note is the script's, the reviewer is the signature.
    return data


def send(sg, review, version, text, frames, images=()):
    """Create the Note and attach each annotated frame. Returns the Note."""
    note = sg.create("Note", note_data(review, version, text, frames))
    for image in images:
        if image and os.path.isfile(image):
            sg.upload("Note", note["id"], image)
    return note


def group_by_version(frames, version_at):
    """[(version, [frames])] in first-seen order; frames with no Version are dropped.

    version_at(frame) -> the Version on screen at that frame, or None.
    """
    groups = {}
    for frame in frames:
        version = version_at(frame)
        if version:
            groups.setdefault(version["id"], (version, []))[1].append(frame)
    return list(groups.values())


def connect(review):
    """A ShotGrid connection, with the API Flow pointed RV at."""
    from flow_dcc import shotgrid
    api = shotgrid.api()
    if api is None:
        raise RuntimeError("shotgun_api3 is not importable inside RV (%s=%r)."
                           % (shotgrid.API_PATH_VAR,
                              os.environ.get(shotgrid.API_PATH_VAR)))
    key = os.environ.get(shotgrid.KEY_VAR)
    if not key:
        raise RuntimeError("%s is not set in this RV session." % shotgrid.KEY_VAR)
    return api.Shotgun(review["site"],
                       script_name=os.environ.get(shotgrid.SCRIPT_VAR)
                       or SCRIPT_DEFAULT,
                       api_key=key)


# -- RV ---------------------------------------------------------------------

def install():
    """Add the Flow menu. Called from rv_player's -pyeval."""
    global _mode
    from rv import commands, rvtypes

    class FlowReview(rvtypes.MinorMode):
        def __init__(self):
            rvtypes.MinorMode.__init__(self)
            self.init("flow-review", None, None,
                      [("Flow", [("Send Annotations to ShotGrid...",
                                  send_from_rv, None,
                                  lambda: commands.NeutralMenuState)])])

    _mode = FlowReview()
    _mode.toggle()


def _annotated_frames():
    """Every frame with a drawing on it, from RV's own Mu helper; [] if none."""
    from rv import runtime
    # shortcut: relies on Mu extra_commands.findAnnotatedFrames (what RV's
    # next/previous annotated frame keys use); unverified on RV 3.1.0, and a
    # failure falls back to the frame on screen.
    try:
        return parse_frames(runtime.eval(
            "require extra_commands; extra_commands.findAnnotatedFrames();", []))
    except Exception:
        return []


def _version_at(frame, versions):
    from rv import commands
    # shortcut: the top source at this frame; a wipe or tile notes the first
    # one -- add a picker if reviewers need to note the one underneath.
    sources = commands.sourcesAtFrame(frame)
    if not sources:
        return None
    media = commands.getStringProperty(sources[0] + ".media.movie")[0]
    return version_for(media, versions)


def _grab_frame(path):
    """The frame as shown, annotations included, written to path."""
    from rv import commands, qtutils
    try:
        commands.exportCurrentFrame(path)
    except Exception:
        pass
    if not os.path.isfile(path):
        # shortcut: the GL view as a fallback -- also catches RV's own HUD if
        # one is showing; drop it once exportCurrentFrame is confirmed on 3.1.
        commands.redraw()
        qtutils.sessionGLView().grabFramebuffer().save(path)
    return path if os.path.isfile(path) else None


def send_from_rv(event=None):
    from rv import commands, qtutils
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets
    parent = qtutils.sessionWindow()
    review = payload()
    versions = review.get("versions") or []
    login = review.get("login") or ""

    current = commands.frame()
    drawn = _annotated_frames()
    groups = group_by_version(drawn or [current],
                              lambda f: _version_at(f, versions))
    if not groups:
        QtWidgets.QMessageBox.warning(
            parent, "Flow",
            "These sources were not opened from a ShotGrid Version in Flow, so "
            "there is no Version to put the annotations on.")
        return

    summary = "\n".join("%s: %s" % (v.get("code"), _frames_label(fs))
                        for v, fs in groups)
    if drawn:
        prompt = ("Annotated frames going to ShotGrid as %s:\n%s\n\n"
                  "Note (optional):" % (signer_name(login) or "you", summary))
    else:
        prompt = ("Nothing is drawn, so the frame on screen goes up:\n%s\n\n"
                  "Note:" % summary)
    text, ok = QtWidgets.QInputDialog.getMultiLineText(
        parent, "Send Annotations to ShotGrid", prompt)
    text = (text or "").strip()
    if not ok or not (text or drawn):
        return

    folder = tempfile.mkdtemp(prefix="flow_annotations_")
    sent = []
    try:
        sg = connect(review)
        for version, frames in groups:
            images = []
            for frame in frames:
                commands.setFrame(frame)
                images.append(_grab_frame(os.path.join(
                    folder, attachment_name(version, frame, login))))
            send(sg, review, version, text, frames, images)
            sent.append(version.get("code") or "")
    except Exception as e:
        done = ("\n\nAlready sent: %s" % ", ".join(sent)) if sent else ""
        QtWidgets.QMessageBox.critical(
            parent, "Flow", "The annotations were not all sent to ShotGrid:"
            "\n\n%s%s" % (e, done))
        return
    finally:
        commands.setFrame(current)
    commands.displayFeedback(
        "Annotations sent to ShotGrid on %s" % ", ".join(sent), 4.0)
