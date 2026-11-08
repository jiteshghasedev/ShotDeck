"""Notes from RV: which Version a source is, and the Note that goes up."""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "rez", "flow_dcc", "startup", "rv"))

import config          # noqa: E402
import fakes           # noqa: E402
import flow_review     # noqa: E402
import rv_player       # noqa: E402

SHOT = {"type": "Shot", "id": 9, "name": "AD1030"}
TASK = {"type": "Task", "id": 77, "name": "Comp"}


def _review(**versions):
    return {"site": "https://x", "project": {"type": "Project", "id": 5},
            "login": "priya", "user": {"type": "HumanUser", "id": 43},
            "versions": [dict(v, code=code) for code, v in versions.items()]}


def test_a_source_finds_its_version_by_path_or_sequence():
    a = {"path": "/jobs/ad/AD1030_v001.%04d.exr", "id": 1}
    b = {"path": "/jobs/ad/AD1030_v002.mov", "id": 2}
    assert flow_review.version_for("/jobs/ad/AD1030_v002.mov", [a, b]) is b
    # RV may hand a sequence back in its own notation.
    assert flow_review.version_for(
        "/jobs/ad/AD1030_v001.1001-1100#.exr", [a, b]) is a
    assert flow_review.version_for("/elsewhere/x.mov", [a, b]) is None
    assert flow_review.version_for("/elsewhere/x.mov", [a]) is a, \
        "one version: it is that one"


def test_annotations_go_up_as_one_signed_note_per_version_with_named_frames():
    review = _review(AD1030_v002={"path": "/a.mov", "id": 2,
                                  "entity": SHOT, "task": TASK})
    version = review["versions"][0]
    folder = tempfile.mkdtemp()
    images = []
    for frame in (1012, 1020):
        images.append(os.path.join(
            folder, flow_review.attachment_name(version, frame, "priya")))
        with open(images[-1], "wb") as f:
            f.write(b"png")
    sg = fakes.FakeShotgun()

    note = flow_review.send(sg, review, version, "Edge flickers here",
                            [1012, 1020], images)
    data = [c for c in sg.calls if c[:2] == ("create", "Note")][0][2]
    assert data["content"] == "Priya: Edge flickers here"
    assert data["subject"] == "AD1030_v002 annotations by Priya, frames 1012, 1020"
    assert {"type": "Version", "id": 2} in data["note_links"]
    assert {"type": "Shot", "id": 9} in data["note_links"]
    assert data["tasks"] == [{"type": "Task", "id": 77}]
    assert "user" not in data, "the script writes it, signed by the reviewer"
    assert [os.path.basename(p) for _, p, _ in sg.uploads] == [
        "AD1030_v002_annotation_f1012_priya.png",
        "AD1030_v002_annotation_f1020_priya.png"]
    assert all(i == note["id"] for i, _, _ in sg.uploads)

    # Drawing with no words still goes up, saying what it is.
    blank = flow_review.note_data(review, version, "", [1012])
    assert blank["content"] == "Priya: Annotations on frame 1012"
    assert blank["subject"] == "AD1030_v002 annotations by Priya, frame 1012"


def test_annotated_frames_are_read_from_rv_and_grouped_by_version():
    assert flow_review.parse_frames("int[] {1020, 1012, 1012}") == [1012, 1020]
    assert flow_review.parse_frames("int[] {}") == []
    a, b = {"id": 1, "code": "A"}, {"id": 2, "code": "B"}
    on_screen = {1: a, 2: b, 3: a, 4: None}
    groups = flow_review.group_by_version([1, 2, 3, 4], on_screen.get)
    assert groups == [(a, [1, 3]), (b, [2])], "frame 4 is no Version: dropped"
    assert flow_review.attachment_name(
        {"id": 7, "code": "AD 1030/v2"}, 5, "") == \
        "AD_1030_v2_annotation_f0005_unknown.png"


def test_rv_gets_the_menu_and_knows_which_version_is_which():
    saved = config.RV_EXECUTABLE, rv_player.reviewer
    config.RV_EXECUTABLE = "/opt/rv"
    rv_player.reviewer = {"login": "priya",
                          "user": {"type": "HumanUser", "id": 43}}
    try:
        _check_menu_and_review_env()
    finally:
        config.RV_EXECUTABLE, rv_player.reviewer = saved


def _check_menu_and_review_env():
    v = {"id": 2, "code": "AD1030_v002", "entity": SHOT, "sg_task": TASK}

    cmd = rv_player.command(["/a.mov"], notes=True)
    at = cmd.index("-pyeval")
    assert "flow_review.install()" in cmd[at + 1]
    compile(cmd[at + 1], "-pyeval", "exec")     # RV runs it as Python
    assert cmd[-1] == "/a.mov", "the media stays last"
    assert "-pyeval" not in rv_player.command(["/a.mov"])

    env = rv_player.review_env([v], ["/a.mov"], {"id": 5})
    review = json.loads(env["FLOW_RV_REVIEW"])
    assert review["login"] == "priya"
    assert review["versions"] == [{"path": "/a.mov", "id": 2,
                                   "code": "AD1030_v002", "entity": SHOT,
                                   "task": TASK}]
    assert flow_review.version_for("/a.mov", review["versions"])["id"] == 2
