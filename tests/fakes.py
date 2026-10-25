"""Stand-ins for ShotGrid and for a project's data.

The fake speaks the same subset of the shotgun_api3 API that SGClient uses, so
the tests exercise the real client and the real service rather than a mock of
them.
"""

import datetime


PROJECT = {"id": 1213, "name": "UAT6", "tank_name": "UAT6"}

SHOT = {"type": "Shot", "id": 2430, "name": "SH010"}

TASK = {
    "id": 7631,
    "content": "Compositing",
    "sg_status_list": "ip",
    "step": {"type": "Step", "id": 9, "name": "Comp"},
    "entity": SHOT,
    "project": PROJECT,
}

TASK_NO_ENTITY = dict(TASK, id=7632, entity=None)

ARTIST = {"type": "HumanUser", "id": 42, "name": "Jitesh",
          "email": "jitesh@5and8.ai",
          "permission_rule_set": {"type": "PermissionRuleSet", "id": 1,
                                  "name": "Artist"}}

PRODUCER = {"type": "HumanUser", "id": 43, "name": "Rahul",
            "email": "rahul@5and8.ai",
            "permission_rule_set": {"type": "PermissionRuleSet", "id": 2,
                                    "name": "Manager"}}

CLIENT = {"type": "HumanUser", "id": 44, "name": "Sam",
          "email": "sam@client.example",
          "permission_rule_set": {"type": "PermissionRuleSet", "id": 3,
                                  "name": "Client"}}


def _schema_field(label, data_type, valid_values=None):
    props = {"valid_values": {"value": valid_values}} if valid_values else {}
    return {"name": {"value": label}, "data_type": {"value": data_type},
            "editable": {"value": True}, "properties": props}


# What schema_field_read("Project") answers: a site with some stock fields and
# none of the custom ones, so the wish-list intersection has something to drop.
PROJECT_SCHEMA = {
    "name": _schema_field("Project Name", "text"),
    "tank_name": _schema_field("Tank Name", "text"),
    "sg_status": _schema_field("Status", "list", ["Active", "Bidding", "Lost"]),
    "sg_description": _schema_field("Description", "text"),
    "start_date": _schema_field("Start Date", "date"),
    "image": _schema_field("Thumbnail", "image"),
    "updated_at": _schema_field("Date Updated", "date_time"),
    "is_template": _schema_field("Template", "checkbox"),
}


TASK_SCHEMA = {
    "content": _schema_field("Task Name", "text"),
    "sg_status_list": _schema_field("Status", "status_list",
                                    ["wtg", "ip", "prw", "cmpt", "fin"]),
    "start_date": _schema_field("Start Date", "date"),
    "due_date": _schema_field("Due Date", "date"),
    "sg_assigned_to": _schema_field("Assigned To", "text"),
    "updated_at": _schema_field("Date Updated", "date_time"),
}


VERSION_SCHEMA = {
    "code": _schema_field("Version Name", "text"),
    "sg_status_list": _schema_field("Status", "status_list", ["rev", "vwd", "apr", "na"]),
    "image": _schema_field("Thumbnail", "image"),
    "filmstrip_image": _schema_field("Filmstrip", "image"),
    "sg_uploaded_movie": _schema_field("Uploaded Movie", "url"),
    "sg_uploaded_movie_mp4": _schema_field("Movie MP4", "url"),
    "project": _schema_field("Project", "entity"),
    "updated_at": _schema_field("Date Updated", "date_time"),
}


class FakeShotgun:
    """Enough of shotgun_api3.Shotgun to publish and to browse."""

    def __init__(self, versions=None):
        self.calls = []
        self.versions = list(versions or [])
        self.notes = []
        self.replies = []
        self.users = [ARTIST, PRODUCER, CLIENT]
        self.projects = [PROJECT]
        self.tasks = []
        self.published_files = []
        self.uploads = []
        self.deleted = []
        self._next_id = 9000
        # Set any of these to an exception to make that operation fail.
        self.fail_create = None
        self.fail_upload = None
        self.fail_delete = None
        self.fail_find = None

    # -- helpers used by the tests ----------------------------------------

    def add_version(self, code, **fields):
        # Each one lands a minute after the last, so "newest first" has
        # something real to sort on.
        seq = len(self.versions)
        version = {
            "type": "Version", "id": self._new_id(), "code": code,
            "created_at": datetime.datetime(2026, 8, 8, 14, 21)
            + datetime.timedelta(minutes=seq),
            "sg_status_list": "rev", "user": ARTIST,
            "project": PROJECT,
            "sg_task": {"type": "Task", "id": TASK["id"]},
            "entity": SHOT,
        }
        version.update(fields)
        self.versions.append(version)
        return version

    def add_note(self, version_id, content, user=None, subject=""):
        note = {
            "type": "Note", "id": self._new_id(), "content": content,
            "subject": subject, "user": user or CLIENT,
            "created_at": datetime.datetime(2026, 8, 9, 16, 32)
            + datetime.timedelta(minutes=len(self.notes)),
            "note_links": [{"type": "Version", "id": version_id}],
        }
        self.notes.append(note)
        return note

    def add_reply(self, note_id, content, user=None):
        reply = {
            "type": "Reply", "id": self._new_id(), "content": content,
            "user": user or ARTIST,
            "created_at": datetime.datetime(2026, 8, 9, 17, 2)
            + datetime.timedelta(minutes=len(self.replies)),
            "entity": {"type": "Note", "id": note_id},
        }
        self.replies.append(reply)
        return reply

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    # -- the API ------------------------------------------------------------

    def find(self, entity_type, filters, fields=None, order=None,
             limit=0, page=1, **kwargs):
        self.calls.append(("find", entity_type, filters, order, limit, page))
        if self.fail_find:
            raise self.fail_find
        if entity_type == "Version":
            rows = [v for v in self.versions if _matches(v, filters)]
            if any(f[0] == "id" and f[1] == "is" for f in filters
                   if not isinstance(f, dict)):
                wanted = next(f[2] for f in filters if f[0] == "id")
                rows = [v for v in self.versions if v["id"] == wanted]
            if order:
                key = order[0]["field_name"]
                rows.sort(key=lambda v: str(v.get(key) or ""),
                          reverse=order[0]["direction"] == "desc")
            if limit:
                start = (page - 1) * limit
                rows = rows[start:start + limit]
            return rows
        if entity_type == "Note":
            return [n for n in self.notes if _matches(n, filters)]
        if entity_type == "Reply":
            return [r for r in self.replies if _matches(r, filters)]
        if entity_type == "HumanUser":
            simple = [f for f in filters if not isinstance(f, dict)]
            wanted = next((f[2] for f in simple if f[0] == "id"), None)
            if wanted is not None:
                return [u for u in self.users if u["id"] in wanted]
            # The bootstrap looks the artist up by email, so the fake has to
            # answer that too or every integration path runs without an owner.
            return [u for u in self.users
                    if all(u.get(f[0]) == f[2] for f in simple)]
        if entity_type == "Project":
            # The status/template filters are the site's business; the fake
            # only ever holds projects a test put there.
            ids = [f[2] for f in filters
                   if not isinstance(f, dict) and f[:2] == ["id", "is"]]
            # copies, as ShotGrid returns a fresh row on every read
            return [dict(p) for p in self.projects if not ids or p["id"] in ids]
        if entity_type == "Task":
            # copies, as ShotGrid returns a fresh row on every read
            rows = [dict(t) for t in self.tasks if _matches(t, filters)]
            if order:
                key = order[0]["field_name"]
                rows.sort(key=lambda t: str(_deep_get(t, key) or ""),
                          reverse=order[0]["direction"] == "desc")
            if limit:
                start = (page - 1) * limit
                rows = rows[start:start + limit]
            return rows
        if entity_type == "Step":
            return [{"type": "Step", "id": 9, "code": "Comp",
                     "entity_type": "Shot"},
                    {"type": "Step", "id": 10, "code": "Lighting",
                     "entity_type": "Shot"},
                    {"type": "Step", "id": 11, "code": "Modeling",
                     "entity_type": "Asset"}]
        return []

    def find_one(self, entity_type, filters, fields=None, **kwargs):
        rows = self.find(entity_type, filters, fields)
        return rows[0] if rows else None

    def create(self, entity_type, data):
        self.calls.append(("create", entity_type, data))
        if self.fail_create:
            raise self.fail_create
        row = dict(data)
        row.update({"type": entity_type, "id": self._new_id()})
        if entity_type == "Version":
            row.setdefault("created_at", datetime.datetime.now())
            self.versions.append(row)
        if entity_type == "PublishedFile":
            self.published_files.append(row)
        if entity_type == "Note":
            row.setdefault("created_at", datetime.datetime.now())
            self.notes.append(row)
        if entity_type == "Reply":
            row.setdefault("created_at", datetime.datetime.now())
            self.replies.append(row)
        return row

    def update(self, entity_type, entity_id, data):
        self.calls.append(("update", entity_type, entity_id, data))
        for row in self.versions + self.notes + self.replies:
            if row["id"] == entity_id and row["type"] == entity_type:
                row.update(data)
                if entity_type == "Version":
                    row["updated_at"] = datetime.datetime.now()
        if entity_type in ("Project", "Task"):
            for row in self.projects if entity_type == "Project" else self.tasks:
                if row["id"] == entity_id:
                    # ShotGrid stamps every write; the conflict check reads it
                    row.update(data, updated_at=datetime.datetime.now())
        return {"type": entity_type, "id": entity_id, **data}

    def delete(self, entity_type, entity_id):
        self.calls.append(("delete", entity_type, entity_id))
        if self.fail_delete:
            raise self.fail_delete
        self.deleted.append((entity_type, entity_id))
        self.versions = [v for v in self.versions if v["id"] != entity_id]
        self.notes = [n for n in self.notes if n["id"] != entity_id]
        self.replies = [r for r in self.replies if r["id"] != entity_id]
        return True

    def upload(self, entity_type, entity_id, path, field_name=None, **kwargs):
        self.calls.append(("upload", entity_type, entity_id, path, field_name))
        if self.fail_upload:
            raise self.fail_upload
        self.uploads.append((entity_id, path, field_name))
        return 555

    def upload_thumbnail(self, entity_type, entity_id, path):
        self.calls.append(("upload_thumbnail", entity_type, entity_id, path))
        return 556

    def summarize(self, entity_type, filters, summary_fields, grouping=None):
        """Counts only, which is all the client asks ShotGrid to add up."""
        self.calls.append(("summarize", entity_type, filters, grouping))
        if self.fail_find:
            raise self.fail_find
        rows = [t for t in self.tasks if _matches(t, filters)] \
            if entity_type == "Task" else []
        out = {"summaries": {"id": len(rows)}}
        if grouping:
            field, groups = grouping[0]["field"], {}
            for row in rows:
                value = row.get(field)
                groups[value] = groups.get(value, 0) + 1
            out["groups"] = [{"group_name": v, "group_value": v,
                              "summaries": {"id": n}}
                             for v, n in groups.items()]
        return out

    def schema_field_read(self, entity_type, field=None):
        self.calls.append(("schema_field_read", entity_type, field))
        if field is None:
            return dict({"Project": PROJECT_SCHEMA, "Task": TASK_SCHEMA,
                         "Version": VERSION_SCHEMA}.get(entity_type, {}))
        return {field: {"properties": {
            "valid_values": {"value": ["wip", "rev", "apr"]},
            "display_values": {"value": {"wip": "Work In Progress",
                                         "rev": "Pending Review",
                                         "apr": "Approved"}},
        }}}


def _matches(version, filters):
    """Only the filter forms the client actually sends."""
    for f in filters or []:
        if isinstance(f, dict):                     # an "any" search group
            if not any(_matches(version, [sub]) for sub in f["filters"]):
                return False
            continue
        field, op, value = f[0], f[1], f[2]
        actual = _deep_get(version, field)
        if op == "is":
            if isinstance(value, dict):
                if isinstance(actual, list):
                    # note_links and friends: a link matches if it is in there
                    if not any(a.get("id") == value["id"] and
                               a.get("type") == value["type"]
                               for a in actual):
                        return False
                elif not actual or actual.get("id") != value["id"]:
                    return False
            elif actual != value:
                return False
        elif op == "in":
            wanted = value if isinstance(value, list) else [value]
            if isinstance(actual, dict):
                if not _links_match([actual], wanted):
                    return False
            elif isinstance(actual, list):
                # A multi-entity field such as note_links: it matches if any
                # of its links is one of the wanted entities.
                if not _links_match(actual, wanted):
                    return False
            elif actual not in wanted:
                return False
        elif op == "not_in":
            if actual in (value if isinstance(value, list) else [value]):
                return False
        elif op == "contains":
            haystack = actual if isinstance(actual, str) else \
                (actual or {}).get("name", "") if isinstance(actual, dict) \
                else ""
            if str(value).lower() not in haystack.lower():
                return False
        elif op == "starts_with":
            if not str(actual or "").lower().startswith(str(value).lower()):
                return False
        elif op in ("less_than", "greater_than"):
            # dates are "YYYY-MM-DD" strings, so they compare as text
            if actual is None or not (actual < value if op == "less_than"
                                      else actual > value):
                return False
        elif op in ("in_last", "in_calendar_day", "between"):
            continue          # dates are the server's business, not the fake's
    return True


def _links_match(links, wanted):
    for link in links:
        if not isinstance(link, dict):
            continue
        for want in wanted:
            if not isinstance(want, dict):
                continue
            if link.get("id") == want.get("id") and \
                    link.get("type") == want.get("type"):
                return True
    return False


def _deep_get(version, field):
    if field in version:
        return version[field]
    # "user.HumanUser.name" style
    parts = field.split(".")
    value = version.get(parts[0])
    if isinstance(value, dict) and len(parts) == 3:
        return value.get(parts[2])
    return None


def add_task(sg, content, project=None, entity_name=None, task_id=None,
             owner=None, step=None):
    """Seed a task assigned to the artist. Returns the task dict."""
    project = project or PROJECT
    task = dict(
        TASK,
        id=task_id if task_id is not None else 7700 + len(sg.tasks),
        content=content,
        project=project,
        entity=dict(SHOT, name=entity_name or SHOT["name"]),
        step=dict(TASK["step"], name=step or content),
        sg_assigned_to=owner or ARTIST["email"],
    )
    sg.tasks.append(task)
    if not any(p["id"] == project["id"] for p in sg.projects):
        sg.projects.append(project)
    return task


def client(sg=None):
    """An SGClient wired to the fake, without touching the network."""
    import sg_client
    c = sg_client.SGClient.__new__(sg_client.SGClient)
    c._reset_caches()
    c.sg = sg or FakeShotgun()
    c._owner = ARTIST
    c._owner_value = ARTIST["email"]
    return c
