"""Projects for the web page: schema, listing, and the guarded write."""

import datetime

import fakes
import sg_client


def _project(**fields):
    stamp = datetime.datetime(2026, 10, 1, 9, 30)
    return dict({"type": "Project", "id": 1300, "name": "SHOW01",
                 "sg_description": "old", "updated_at": stamp}, **fields)


def test_schema_is_read_once_per_client():
    sg = fakes.FakeShotgun()
    client = fakes.client(sg)
    client.entity_schema("Project")
    client.entity_schema("Project")
    assert [c for c in sg.calls if c[0] == "schema_field_read"] == [
        ("schema_field_read", "Project", None)]


def test_update_writes_and_returns_what_shotgrid_holds():
    sg = fakes.FakeShotgun()
    project = _project()
    sg.projects = [project]
    before, after = fakes.client(sg).update_project(
        1300, {"sg_description": "new"}, project["updated_at"].isoformat(),
        ["sg_description", "updated_at"])
    assert before["sg_description"] == "old"
    assert after["sg_description"] == "new"
    assert ("update", "Project", 1300, {"sg_description": "new"}) in sg.calls


def test_a_stale_update_is_refused_and_nothing_is_written():
    sg = fakes.FakeShotgun()
    sg.projects = [_project()]
    try:
        fakes.client(sg).update_project(
            1300, {"sg_description": "new"}, "2026-09-01T00:00:00",
            ["sg_description", "updated_at"])
    except sg_client.UpdateConflict as e:
        assert e.current["sg_description"] == "old"
    else:
        raise AssertionError("a stale write went through")
    assert not [c for c in sg.calls if c[0] == "update"]


def test_only_editable_fields_may_be_written():
    sg = fakes.FakeShotgun()
    sg.projects = [_project()]
    try:
        fakes.client(sg).update_project(1300, {"sg_status": "Lost"}, None,
                                        ["sg_status"])
    except ValueError as e:
        assert "sg_status" in str(e)
    else:
        raise AssertionError("sg_status was writable")
    assert not [c for c in sg.calls if c[0] == "update"]


def test_a_missing_project_is_a_lookup_error():
    try:
        fakes.client().update_project(4040, {"sg_description": "x"}, None,
                                      ["sg_description"])
    except LookupError:
        pass
    else:
        raise AssertionError("updated a project that does not exist")
