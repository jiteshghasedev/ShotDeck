"""Tasks for the web page: exact assignee matching, paging, counts."""

import fakes


def _seed(sg):
    for owner in ["al@5and8.ai", "sal@5and8.ai", "bo@5and8.ai, al@5and8.ai",
                  "bo@5and8.ai;al@5and8.ai", "bo@5and8.ai"]:
        fakes.add_task(sg, "Comp", owner=owner)


def test_assignee_filter_matches_the_person_not_a_longer_name():
    sg = fakes.FakeShotgun()
    _seed(sg)
    c = fakes.client(sg)
    flt = c.assigned_to_filter(None, "al@5and8.ai")
    rows, total = c.task_page([flt], ["sg_assigned_to"], None, 1, 50)
    assert total == 3
    assert "sal@5and8.ai" not in [t["sg_assigned_to"] for t in rows]


def test_a_list_field_is_matched_with_is_as_shotgrid_requires():
    sg = fakes.FakeShotgun()
    _seed(sg)
    c = fakes.client(sg)
    c._owner_rel = "is"                       # what _owner_relation finds for a List field
    flt = c.assigned_to_filter(None, "al@5and8.ai")
    assert flt == ["sg_assigned_to", "is", "al@5and8.ai"]
    assert c.task_page([flt], ["id"], None, 1, 50)[1] == 1


def test_owner_value_falls_back_to_the_email():
    c = fakes.client()
    owner, value = c.owner_for(fakes.ARTIST["email"])
    assert owner["id"] == fakes.ARTIST["id"] and value == fakes.ARTIST["email"]
    assert c.owner_for("nobody@5and8.ai") == (None, "nobody@5and8.ai")


def test_a_page_and_the_total_take_two_calls():
    sg = fakes.FakeShotgun()
    _seed(sg)
    rows, total = fakes.client(sg).task_page(
        [], ["content"], [{"field_name": "id", "direction": "asc"}], 2, 2)
    assert total == 5 and len(rows) == 2
    assert [c[0] for c in sg.calls] == ["find", "summarize"]


def test_counts_are_grouped_by_status():
    sg = fakes.FakeShotgun()
    _seed(sg)
    sg.tasks[0]["sg_status_list"] = "fin"
    assert fakes.client(sg).task_counts([]) == {"fin": 1, "ip": 4}


def _stamped(sg):
    import datetime
    task = fakes.add_task(sg, "Comp")
    task["updated_at"] = datetime.datetime(2026, 10, 1, 9, 30)
    return task


def test_a_status_change_posts_the_note_first_then_writes():
    sg = fakes.FakeShotgun()
    task = _stamped(sg)
    c = fakes.client(sg)
    c.login = "jitesh"
    before, after = c.update_task(task["id"], {"sg_status_list": "prw"},
                                  task["updated_at"].isoformat(),
                                  ["sg_status_list", "updated_at", "project", "entity"],
                                  note="Edge fixed")
    assert before["sg_status_list"] == "ip" and after["sg_status_list"] == "prw"
    order = [x[:2] for x in sg.calls if x[0] in ("create", "update")]
    assert order == [("create", "Note"), ("update", "Task")]
    assert sg.notes[0]["content"] == "Jitesh: Edge fixed"


def test_a_stale_task_write_is_refused_before_any_note():
    import sg_client
    sg = fakes.FakeShotgun()
    task = _stamped(sg)
    try:
        fakes.client(sg).update_task(task["id"], {"sg_status_list": "prw"},
                                     "2020-01-01T00:00:00", ["updated_at"], note="x")
    except sg_client.UpdateConflict:
        pass
    else:
        raise AssertionError("stale write went through")
    assert not [x for x in sg.calls if x[0] in ("create", "update")]


def test_only_editable_task_fields_may_be_written():
    sg = fakes.FakeShotgun()
    task = _stamped(sg)
    try:
        fakes.client(sg).update_task(task["id"], {"content": "x"}, None, ["content"])
    except ValueError:
        pass
    else:
        raise AssertionError("content was writable")


def test_after_change_never_raises_and_leaves_review_mail_to_shotgrid():
    import config
    import status_mail
    assert "ShotGrid" in status_mail.after_change(
        None, fakes.TASK, "Jitesh", "", "", "ip", config.REVIEW_MAIL_STATUS)
    saved = status_mail._send_email

    def broken():
        raise RuntimeError("SMTP down")
    status_mail._send_email = lambda: lambda *a: broken()
    try:
        line = status_mail.after_change(None, fakes.TASK, "Jitesh", "", "",
                                        "ip", config.PRODUCTION_MAIL_STATUSES[0])
    finally:
        status_mail._send_email = saved
    assert "mail failed" in line or "nobody" in line
