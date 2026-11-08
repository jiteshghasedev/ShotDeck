"""The DCC Task panel: what it reads from ShotGrid and what it writes back."""

import datetime
import os
import sys

import config

sys.path.insert(0, config.DCC_SOURCE_ROOT)

from flow_dcc import task_panel  # noqa: E402

SHOT = {"type": "Shot", "id": 9, "name": "AD1030"}
TASK = {"type": "Task", "id": 42, "content": "Comp", "sg_status_list": "ip",
        "entity": SHOT, "project": {"type": "Project", "id": 5},
        "sg_assigned_to": "jitesh@5and8.ai; anu@5and8.ai",
        "task_reviewers": [{"type": "HumanUser", "id": 3, "name": "Priya"}],
        "due_date": "2026-11-20"}
SCRIPT = {"type": "ApiUser", "id": 1, "name": "SG_daemon"}


class FakeSG:
    """Rows by entity type; records every call."""

    def __init__(self):
        self.calls = []
        self.rows = {
            "Task": [dict(TASK)],
            "Version": [{"type": "Version", "id": 2, "code": "AD1030_comp_v002",
                         "sg_status_list": "rev", "image": None,
                         "user": {"type": "HumanUser", "id": 7, "name": "Jitesh"},
                         "created_at": datetime.datetime(2026, 11, 8, 14, 5)}],
            "Note": [{"type": "Note", "id": 100, "user": SCRIPT,
                      "subject": "AD1030_comp_v002 annotations by Priya, frame 1012",
                      "content": "Priya: Edge flickers here",
                      "attachments": [{"type": "Attachment", "id": 8,
                                       "name": "AD1030_comp_v002_annotation_f1012_priya.png"}],
                      "created_at": datetime.datetime(2026, 11, 8, 15, 0)},
                     {"type": "Note", "id": 101, "subject": "",
                      "user": {"type": "HumanUser", "id": 3, "name": "Priya S"},
                      "content": "Written on the site", "attachments": [],
                      "created_at": datetime.datetime(2026, 11, 7, 9, 0)}],
            "Reply": [{"type": "Reply", "id": 500, "user": SCRIPT,
                       "entity": {"type": "Note", "id": 100},
                       "content": "Jitesh: Fixed in v003",
                       "created_at": datetime.datetime(2026, 11, 8, 16, 0)}],
        }

    def find(self, entity_type, filters, fields=None, order=None, limit=0):
        self.calls.append(("find", entity_type, filters))
        return [dict(r) for r in self.rows.get(entity_type, [])]

    def find_one(self, entity_type, filters, fields=None):
        rows = self.find(entity_type, filters, fields)
        return rows[0] if rows else None

    def create(self, entity_type, data):
        self.calls.append(("create", entity_type, data))
        return dict(data, type=entity_type, id=900)

    def update(self, entity_type, entity_id, data):
        self.calls.append(("update", entity_type, entity_id, data))
        return dict(data, type=entity_type, id=entity_id)

    def schema_field_read(self, entity_type, field=None):
        return {"sg_status_list": {"properties": {
            "valid_values": {"value": ["ip", "prw", "nts"]},
            "display_values": {"value": {"ip": "In Progress",
                                         "prw": "Pending Review"}}}}}

    def download_attachment(self, attachment, file_path=None):
        self.calls.append(("download", attachment["id"], file_path))
        with open(file_path, "wb") as f:
            f.write(b"png")
        return file_path


def test_load_reads_the_task_its_versions_and_their_notes_with_replies():
    sg = FakeSG()
    data = task_panel.load(sg, 42)
    assert data["task"]["content"] == "Comp"
    assert [v["code"] for v in data["versions"]] == ["AD1030_comp_v002"]

    note_filter = [c for c in sg.calls if c[:2] == ("find", "Note")][0][2]
    either = note_filter[0]
    assert either["filter_operator"] == "any"
    assert ["tasks", "is", {"type": "Task", "id": 42}] in either["filters"]
    assert ["note_links", "in", [{"type": "Version", "id": 2}]] in either["filters"]

    first, second = data["threads"]
    assert first["author"] == "Priya", "the signature, not the script user"
    assert first["text"] == "Edge flickers here"
    assert first["when"] == "2026-11-08 15:00"
    assert len(first["images"]) == 1
    assert first["replies"] == [{"id": 500, "author": "Jitesh",
                                 "when": "2026-11-08 16:00",
                                 "text": "Fixed in v003"}]
    assert second["author"] == "Priya S", "unsigned: the ShotGrid user"
    assert second["replies"] == []


def test_a_task_with_no_versions_reads_only_its_own_notes():
    sg = FakeSG()
    sg.rows["Version"] = []
    task_panel.load(sg, 42)
    note_filter = [c for c in sg.calls if c[:2] == ("find", "Note")][0][2]
    assert note_filter == [["tasks", "is", {"type": "Task", "id": 42}]]


def test_people_and_statuses_read_as_the_site_has_them():
    assert task_panel.people(TASK["sg_assigned_to"]) == \
        "jitesh@5and8.ai, anu@5and8.ai"
    assert task_panel.people(TASK["task_reviewers"]) == "Priya"
    assert task_panel.people(None) == ""
    assert task_panel.statuses(FakeSG()) == [
        ("ip", "In Progress"), ("prw", "Pending Review"), ("nts", "nts")]


def test_replies_and_status_notes_are_signed_by_the_artist():
    sg = FakeSG()
    task_panel.reply(sg, 100, "  Fixed in v003 ", "jitesh")
    created = [c for c in sg.calls if c[0] == "create"]
    assert created[-1][1:] == ("Reply", {"entity": {"type": "Note", "id": 100},
                                         "content": "Jitesh: Fixed in v003"})
    try:
        task_panel.reply(sg, 100, "   ", "jitesh")
        assert False, "an empty reply must not be sent"
    except ValueError:
        pass

    task_panel.set_status(sg, TASK, "prw", "Ready for review", "jitesh")
    note = [c for c in sg.calls if c[:2] == ("create", "Note")][-1][2]
    assert note["content"] == "Jitesh: Ready for review"
    assert note["subject"] == "Set to prw"
    assert note["tasks"] == [{"type": "Task", "id": 42}]
    assert note["note_links"] == [{"type": "Shot", "id": 9}]
    # The note goes up before the status changes, as in Flow.
    assert sg.calls[-1] == ("update", "Task", 42, {"sg_status_list": "prw"})

    before = len(sg.calls)
    task_panel.set_status(sg, TASK, "ip", "", "jitesh")
    assert [c[0] for c in sg.calls[before:]] == ["update"], "no note: no Note"


def test_a_notes_images_are_downloaded_under_their_own_names():
    sg = FakeSG()
    thread = task_panel.load(sg, 42)["threads"][0]
    folder = task_panel.download_images(sg, thread)
    assert os.listdir(folder) == ["AD1030_comp_v002_annotation_f1012_priya.png"]


def test_the_panel_window_builds_and_shows_the_notes():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841

    class Ctx:
        task_id, entity_name, task_name, user = 42, "AD1030", "Comp", "jitesh"

    sg = FakeSG()
    panel = task_panel._panel_class()(sg, Ctx())
    assert panel.notes.topLevelItemCount() == 2
    assert panel.versions.topLevelItemCount() == 1
    assert panel.status.currentData() == "ip"
    assert "Fixed in v003" in panel.thread.toPlainText()
    assert panel.images_btn.isEnabled()
    assert "Priya" in panel.header.text()

    panel.reply_box.setPlainText("On it")
    panel._reply()
    assert ("create", "Reply", {"entity": {"type": "Note", "id": 100},
                                "content": "Jitesh: On it"}) in sg.calls
    assert panel.reply_box.toPlainText() == ""
    panel.close()
