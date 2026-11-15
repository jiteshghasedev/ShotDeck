"""A lead's sequence review: which shots, where annotations go, and the Note
that waits on disk while ShotGrid is down."""

import datetime
import os
import sys
import tempfile

import config

sys.path.insert(0, config.DCC_SOURCE_ROOT)

from flow_dcc import context, review  # noqa: E402

SEQ = {"type": "Sequence", "id": 3}
SHOTS = [{"type": "Shot", "id": i, "code": c, "sg_sequence": SEQ}
         for i, c in ((10, "AD1030"), (11, "AD1008"), (12, "AD1050"))]
LEAD = {"type": "HumanUser", "id": 7, "name": "Nanda Kumar"}


class _SG(object):
    """Answers the four reads review.shots() makes, and records writes."""

    def __init__(self, fail=0):
        self.notes, self.uploads, self.fail = [], [], fail

    def find_one(self, kind, filters, fields=None):
        if kind == "HumanUser":
            return LEAD
        return next(s for s in SHOTS if s["id"] == filters[0][2])

    def find(self, kind, filters, fields=None):
        if kind == "Shot":
            return list(SHOTS)
        assert filters[1] == [review.LEAD_FIELD, "is",
                              {"type": "HumanUser", "id": 7}]
        return [{"entity": {"type": "Shot", "id": 11},
                 "step": {"name": "Comp"}},
                {"entity": {"type": "Shot", "id": 12},
                 "step": {"name": "Matchmove"}}]

    def create(self, kind, data):
        self.notes.append(data)
        return {"id": 900 + len(self.notes)}

    def upload(self, kind, note_id, path):
        if self.fail:
            self.fail -= 1
            raise IOError("site down")
        self.uploads.append((note_id, os.path.basename(path)))


def _ctx(root):
    os.environ.update(FLOW_USER_EMAIL="nanda@5and8.ai")
    ctx = context.Context()
    ctx.entity_root = root + "/sequences/SEQ001/shots/AD1030"
    ctx.entity_type, ctx.entity_id, ctx.sequence = "Shot", 10, "SEQ001"
    ctx.artist, ctx.department, ctx.project_id = "nanda", "roto", 5
    ctx.project_name = "UAT6"            # an empty Context is falsy
    return ctx


def test_only_reviewed_shots_load_unless_everything_is_asked_for():
    ctx = _ctx("/jobs/UAT6")
    rows = review.shots(_SG(), ctx)
    assert [(r["code"], r["department"]) for r in rows] == \
        [("AD1008", "comp"), ("AD1050", "matchmove")]
    assert rows[0]["root"] == "/jobs/UAT6/sequences/SEQ001/shots/AD1008"
    every = review.shots(_SG(), ctx, everything=True)
    assert [(r["code"], r["department"]) for r in every] == \
        [("AD1008", "comp"), ("AD1030", "roto"), ("AD1050", "matchmove")]


def test_annotations_and_the_review_script_have_their_own_folders():
    root = tempfile.mkdtemp().replace("\\", "/")
    ctx = _ctx(root)
    when = datetime.datetime(2026, 11, 15, 14, 30)
    assert review.annotation_path("AD1050", "matchmove", when, ctx) == (
        root + "/sequences/SEQ001/shots/AD1050/elements/images/annotation/MM/"
        "AD1050_nanda_MM_annot_20261115_1430/"
        "AD1050_nanda_MM_annot_20261115_1430.%04d.jpg")
    first = review.script_path(ctx)
    assert first == root + "/sequences/SEQ001/nuke/review/" \
        "SEQ001_review_nanda_v0001.nk"
    os.makedirs(os.path.dirname(first))
    open(first, "w").close()
    assert review.script_path(ctx).endswith("SEQ001_review_nanda_v0002.nk")


def test_a_note_that_fails_waits_on_disk_and_never_posts_twice():
    root = tempfile.mkdtemp()
    review.QUEUE_DIR = os.path.join(root, "pending")
    images = []
    for frame in (1012, 1013):
        images.append(os.path.join(root, "a.%d.jpg" % frame))
        open(images[-1], "w").close()
    ctx = _ctx("/jobs/UAT6")
    entry = review.note_entry({"id": 11, "code": "AD1008"}, "comp", images,
                              "Soften the edge", ctx)
    assert entry["subject"] == "AD1008 comp annotations by Nanda"
    assert entry["content"] == "Nanda: Soften the edge"

    down = _SG(fail=1)                  # the Note is made, a frame fails
    posted, reason = review.send_or_queue(lambda: down, entry)
    assert not posted and "site down" in reason
    assert len(review.pending()) == 1

    def offline():
        raise IOError("no network")
    assert review.retry(offline) == (0, 1, "no network")

    assert review.retry(lambda: down) == (1, 0, "")
    assert len(down.notes) == 1          # one Note, both frames on it
    assert down.uploads == [(901, "a.1012.jpg"), (901, "a.1013.jpg")]
    assert review.pending() == []
