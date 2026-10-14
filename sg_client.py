import os
import threading

import shotgun_api3

import re

import applog
import config
import notes_service

log = applog.get()

# Whose tasks a list holds: the artist's own, the ones they lead or review
# (config.TASK_LEAD_FIELD), or every artist's.
MINE, REVIEW, EVERYONE = "mine", "review", "everyone"


def _newer(candidate, current, field):
    """Compare on the configured field, tolerating a site that leaves it null.

    The order= on the query already sorts; this only decides ties and copes
    with rows the sort could not place.
    """
    left, right = candidate.get(field), current.get(field)
    if left is None:
        return False
    if right is None:
        return True
    try:
        return left > right
    except TypeError:                                # pragma: no cover
        return str(left) > str(right)


def _media_fields(info):
    """Stock Version fields worth filling in from an inspected media file.

    Kept separate from the required data because these are the fields most
    likely to have been renamed or removed on a given site.
    """
    if not info:
        return {}
    data = {}
    if getattr(info, "frames", None):
        data["frame_count"] = info.frames
        data["sg_first_frame"] = 1
        data["sg_last_frame"] = info.frames
        data["frame_range"] = f"1-{info.frames}"
    if getattr(info, "path", None):
        key = ("sg_path_to_movie" if getattr(info, "kind", "") == "movie"
               else "sg_path_to_frames")
        data[key] = info.path
    return data


class SGClient:
    # The signed-in AD login. Every write goes in as the one script user, so
    # notes carry this in their text to say who wrote them. Set by MainWindow.
    login = None

    def __init__(self):
        self._reset_caches()
        self._local = threading.local()
        if os.environ.get("SGDESK_DEV") == "1":
            from sgdesk_dcc.devkit.mock_sg import MockShotgun
            self.sg = MockShotgun()
            return
        if not config.SG_SCRIPT_KEY:
            raise RuntimeError(
                "SG_SCRIPT_KEY is not set. Export it before starting Flow "
                )

    @property
    def sg(self):
        """The ShotGrid connection for the calling thread.

        One connection per thread, not one per client. shotgun_api3 keeps a
        single httplib2 connection per instance and is not thread-safe: two
        ui/jobs.py workers sharing one -- the review query and the latest-version
        query, say -- can have one thread reading a response while the other
        closes the same SSL socket. That corrupts the heap and aborts the
        process ("free(): invalid next size"), with no Python traceback beyond
        what faulthandler prints.

        A lock would also be correct, but it would serialise the four queries
        the bootstrap deliberately runs at once. Pool threads are reused, so
        the number of connections stays bounded by the pool size.
        """
        override = getattr(self, "_sg_override", None)
        if override is not None:
            return override

        local = getattr(self, "_local", None)
        if local is None:                    # built by fakes without __init__
            local = self._local = threading.local()

        conn = getattr(local, "conn", None)
        if conn is None:
            conn = local.conn = shotgun_api3.Shotgun(
                config.SG_SITE,
                script_name=config.SG_SCRIPT_NAME,
                api_key=config.SG_SCRIPT_KEY,
            )
            log.debug("opened a ShotGrid connection for thread %s",
                      threading.current_thread().name)
        return conn

    @sg.setter
    def sg(self, value):
        """Pin one connection for every thread: the dev mock and the tests."""
        self._sg_override = value

    def _reset_caches(self):
        self._owner = None
        self._owner_value = None
        self._statuses = None            # Task statuses
        self._version_statuses = None    # Version statuses
        self._steps = None               # pipeline steps, aka departments
        self._version_field_cache = None
        self._optional_fields = []       # the ones this site turned out to have
        self._users = {}                 # id -> HumanUser, for note authors

    @property
    def api_identity(self):
        """Which ShotGrid script the API calls authenticate as.

        Shown in the publish result so a supervisor can tell at a glance that a
        Version came through Flow's daemon rather than a seat. Only the
        script *name* -- the key never leaves config.
        """
        return config.SG_SCRIPT_NAME

    # -- user / owner -----------------------------------------------------

    def resolve_owner(self, user_email):
        """Look up the user entity for this email address.

        In entity mode the entity itself is used in the task filter. In string
        mode (the default here) it is only used to obtain the value that
        Task.sg_assigned_to stores -- see config.TASK_OWNER_STRING_FIELD.
        """
        fields = [config.TASK_OWNER_MATCH_FIELD, "name"]
        if config.TASK_OWNER_STRING_FIELD not in fields:
            fields.append(config.TASK_OWNER_STRING_FIELD)
        self._owner = self.sg.find_one(
            config.TASK_OWNER_ENTITY,
            [[config.TASK_OWNER_MATCH_FIELD, "is", user_email]],
            fields,
        )
        self._owner_value = self._owner_string(user_email)
        return self._owner

    def _owner_string(self, user_email):
        """The value to compare Task.sg_assigned_to against, in string mode."""
        if config.TASK_OWNER_IS_ENTITY:
            return None
        if self._owner:
            value = self._owner.get(config.TASK_OWNER_STRING_FIELD)
            if value:
                return value
        # No matching entity, or the field is empty on it: the email address is
        # still worth trying, since that is what the tasks most likely store.
        return user_email

    @property
    def owner(self):
        return self._owner

    @property
    def owner_value(self):
        return self._owner_value

    # -- queries -----------------------------------------------------------

    def active_projects(self):
        return self.sg.find(
            "Project",
            [["sg_status", "is", "Active"], ["is_template", "is", False]],
            config.PROJECT_FIELDS,
            order=[{"field_name": "name", "direction": "asc"}],
        )

    def software_for_project(self, project):
        """Software active + either global (no project links) or linked to this project."""
        sws = self.sg.find(
            "Software",
            [["sg_status_list", "is", "act"]],
            config.SOFTWARE_FIELDS,
        )
        out = []
        for sw in sws:
            links = sw.get("projects") or []
            if links and not any(p["id"] == project["id"] for p in links):
                continue
            # Launchable means either a real path, or a rez request that
            # supplies the command itself.
            if sw.get("linux_path") or sw.get(config.SOFTWARE_REZ_FIELD):
                out.append(sw)
        return out

    # -- status ------------------------------------------------------------

    def task_statuses(self):
        """[(code, label), ...] straight from the site's status list.

        Read once and kept -- the schema does not change while Flow is
        open, and this is called every time a context menu opens.
        """
        if self._statuses is not None:
            return self._statuses

        try:
            schema = self.sg.schema_field_read("Task", "sg_status_list")
            props = schema["sg_status_list"]["properties"]
            codes = props["valid_values"]["value"]
            # display_values is a code -> label map on most sites, but it is
            # not guaranteed to be there.
            labels = (props.get("display_values") or {}).get("value") or {}
        except Exception as e:
            log.warning("could not read the Task status list: %s", e)
            self._statuses = []
            return self._statuses

        self._statuses = [(c, labels.get(c, c)) for c in codes]
        return self._statuses

    def set_task_status(self, task_id, code):
        """Write a new status back to ShotGrid. Returns the updated task."""
        log.info("setting task %s status to %s", task_id, code)
        return self.sg.update("Task", task_id, {"sg_status_list": code})

    def set_status(self, entity_type, entity_id, code):
        log.info("setting %s %s status to %s", entity_type, entity_id, code)
        return self.sg.update(entity_type, entity_id, {"sg_status_list": code})

    def approved_shot_versions(self, project):
        """The newest approved Version per shot in the project, sorted by
        sequence then shot. One query; the reduction happens here."""
        rows = self.sg.find(
            "Version",
            [["project", "is", {"type": "Project", "id": project["id"]}],
             ["sg_status_list", "in", list(config.DELIVERY_APPROVED_STATUSES)]],
            ["code", "created_at", "sg_status_list", "entity",
             "entity.Shot.sg_sequence", config.VERSION_TASK_FIELD],
            order=[{"field_name": "created_at", "direction": "desc"}],
        )
        latest = {}
        for row in rows:
            shot = row.get("entity") or {}
            if shot.get("type") != "Shot" or shot["id"] in latest:
                continue
            latest[shot["id"]] = row
        return sorted(latest.values(), key=lambda v: (
            (v.get("entity.Shot.sg_sequence") or {}).get("name") or "",
            v["entity"].get("name") or ""))

    # -- publishing --------------------------------------------------------

    def versions_for_task(self, task_id):
        return self.sg.find(
            "Version",
            [[config.VERSION_TASK_FIELD, "is",
              {"type": "Task", "id": task_id}]],
            ["code", "created_at"],
            order=[{"field_name": "created_at", "direction": "desc"}],
        )

    def latest_versions_for_tasks(self, task_ids):
        """{task_id: newest Version} for a whole page of tasks, in one query.

        One `find` with the task ids in an `in` filter, reduced here -- a query
        per task is the N+1 that makes a task list crawl on a busy show.
        "Newest" is ShotGrid's timestamp, not the version name: v010, v011,
        v002 is a real publishing order and max(name) reads it backwards.
        """
        task_ids = [i for i in (task_ids or []) if i]
        if not task_ids:
            return {}

        field = config.LATEST_VERSION_FIELD
        rows = self.sg.find(
            "Version",
            [[config.VERSION_TASK_FIELD, "in",
              [{"type": "Task", "id": i} for i in task_ids]]],
            config.LATEST_VERSION_FIELDS,
            order=[{"field_name": field, "direction": "desc"}],
        )

        latest = {}
        for row in rows:
            task_id = (row.get(config.VERSION_TASK_FIELD) or {}).get("id")
            if task_id is None:
                continue
            best = latest.get(task_id)
            if best is None or _newer(row, best, field):
                latest[task_id] = row
        return latest

    # -- version browser ---------------------------------------------------

    def versions(self, entity=None, task_id=None, filters=None,
                 order=None, limit=0, page=1, fields=None):
        """Versions for a shot or a task, filtered on the server.

        `filters` is raw ShotGrid filter syntax so callers can push whatever
        the UI is asking for down to the API rather than fetching everything
        and sifting it here -- a busy shot has thousands of versions.
        """
        found = list(filters or [])
        if entity:
            found.append([config.VERSION_ENTITY_FIELD, "is",
                          {"type": entity["type"], "id": entity["id"]}])
        if task_id:
            found.append([config.VERSION_TASK_FIELD, "is",
                          {"type": "Task", "id": task_id}])
        order = order or [{"field_name": "created_at", "direction": "desc"}]
        try:
            return self.sg.find(
                "Version", found, fields or self._version_fields(),
                order=order, limit=limit, page=page)
        except Exception as e:
            if fields or not self._optional_fields:
                raise
            # A field this site does not have fails the whole query. Rather
            # than leaving the browser looking broken, drop the optional ones
            # and carry on with less detail.
            log.warning("version query failed with the optional fields (%s); "
                        "retrying with the core fields only", e)
            self._optional_fields = []
            self._version_field_cache = list(config.VERSION_FIELDS)
            return self.sg.find(
                "Version", found, self._version_field_cache,
                order=order, limit=limit, page=page)

    def _version_fields(self):
        """VERSION_FIELDS, plus any optional field this site turns out to have.

        Asking for a field the site does not have fails the whole query, so the
        optional list is probed once and then remembered.
        """
        if self._version_field_cache is not None:
            return self._version_field_cache

        fields = list(config.VERSION_FIELDS)
        self._optional_fields = []
        for name in config.VERSION_OPTIONAL_FIELDS:
            try:
                if self.sg.schema_field_read("Version", name):
                    fields.append(name)
                    self._optional_fields.append(name)
            except Exception as e:
                log.info("Version.%s not available on this site (%s)", name, e)
        self._version_field_cache = fields
        return fields

    def compare_pair(self, version_id):
        """(this version, the one published before it on the same task).

        Two queries: the version itself, then its task's versions. The second
        is ordered by the same timestamp the Latest Version column uses, so
        "previous" means the same thing everywhere.
        """
        version = self.sg.find_one(
            "Version", [["id", "is", version_id]], self._version_fields())
        if not version:
            raise RuntimeError(f"Version {version_id} no longer exists.")

        task = version.get(config.VERSION_TASK_FIELD) or {}
        if not task.get("id"):
            return version, None

        field = config.LATEST_VERSION_FIELD
        siblings = self.sg.find(
            "Version",
            [[config.VERSION_TASK_FIELD, "is",
              {"type": "Task", "id": task["id"]}]],
            self._version_fields(),
            order=[{"field_name": field, "direction": "desc"}])

        previous = None
        for row in siblings:
            if row["id"] == version_id:
                continue
            if _newer(version, row, field):
                previous = row
                break
        return version, previous

    def version_statuses(self):
        """[(code, label), ...] from the site's own Version status list."""
        if self._version_statuses is not None:
            return self._version_statuses
        try:
            schema = self.sg.schema_field_read("Version", "sg_status_list")
            props = schema["sg_status_list"]["properties"]
            codes = props["valid_values"]["value"]
            labels = (props.get("display_values") or {}).get("value") or {}
        except Exception as e:
            log.warning("could not read the Version status list: %s", e)
            self._version_statuses = []
            return self._version_statuses
        self._version_statuses = [(c, labels.get(c, c)) for c in codes]
        return self._version_statuses

    def steps(self):
        """Pipeline steps, which is what the browser calls Department."""
        if self._steps is not None:
            return self._steps
        try:
            self._steps = self.sg.find(
                "Step", [], ["code", "short_name", "entity_type"],
                order=[{"field_name": "code", "direction": "asc"}])
        except Exception as e:
            log.warning("could not read the Step list: %s", e)
            self._steps = []
        return self._steps

    # -- version writes ----------------------------------------------------

    def version_exists(self, project, code, task_id=None):
        """The Version with this name, if the site already has one.

        Checked against ShotGrid rather than the list the dialog happens to
        hold, so two artists publishing at the same moment cannot both believe
        v004 is free.
        """
        filters = [
            ["project", "is", {"type": "Project", "id": project["id"]}],
            ["code", "is", code],
        ]
        if task_id:
            filters.append([config.VERSION_TASK_FIELD, "is",
                            {"type": "Task", "id": task_id}])
        return self.sg.find_one("Version", filters, ["code", "created_at"])

    def create_version(self, project, task, name, description="",
                       media_info=None):
        """Create the Version row itself. No media yet.

        Authenticated as the script user (SG_SCRIPT_NAME), while `user` is set
        to the artist resolved at startup -- the API identity and the credited
        artist are two different things and must stay that way.
        """
        data = self._version_data(project, task, name, description)
        extra = _media_fields(media_info)
        if extra:
            try:
                return self.sg.create("Version", dict(data, **extra))
            except Exception as e:
                # Frame and resolution fields are stock but not universal, and
                # one missing field fails the whole create. The Version matters
                # more than the metadata.
                log.warning("create with media fields %s failed (%s); "
                            "creating without them", sorted(extra), e)
        return self.sg.create("Version", data)

    def _version_data(self, project, task, name, description):
        entity = (task or {}).get("entity")
        data = {
            "project": {"type": "Project", "id": project["id"]},
            "code": name,
            # Signed like a note, "Jitesh: Added for first pass".
            "description": notes_service.sign(description, self.login)
            if description else "",
            config.VERSION_TASK_FIELD: {"type": "Task", "id": task["id"]},
        }
        if entity:
            data[config.VERSION_ENTITY_FIELD] = {
                "type": entity["type"], "id": entity["id"]}
        if config.VERSION_STATUS:
            data["sg_status_list"] = config.VERSION_STATUS
        if self._owner:
            data["user"] = {"type": self._owner["type"],
                            "id": self._owner["id"]}
        return data

    def upload_media(self, version_id, path):
        """Upload the media the ShotGrid player streams."""
        return self.sg.upload("Version", version_id, path,
                              field_name=config.VERSION_MEDIA_FIELD)

    def upload_version_thumbnail(self, version_id, path):
        return self.sg.upload_thumbnail("Version", version_id, path)

    def delete_version(self, version_id):
        """Retire a Version -- used to clean up after a cancelled publish."""
        log.info("deleting Version %s", version_id)
        return self.sg.delete("Version", version_id)

    # -- notes -------------------------------------------------------------
    #
    # ShotGrid's own model: a Note links to entities through note_links, and
    # replies are Reply entities pointing back at the Note. Flow stores
    # nothing of its own -- production notes belong to ShotGrid.

    def notes_for_version(self, version_id):
        return self.sg.find(
            "Note",
            [["note_links", "is", {"type": "Version", "id": version_id}]],
            config.NOTE_FIELDS,
            order=[{"field_name": "created_at", "direction": "asc"}],
        )

    def notes_for_versions(self, version_ids):
        """Notes on any of these versions, in one query rather than one each."""
        if not version_ids:
            return []
        return self.sg.find(
            "Note",
            [["note_links", "in",
              [{"type": "Version", "id": i} for i in version_ids]]],
            config.NOTE_FIELDS,
            order=[{"field_name": "created_at", "direction": "desc"}],
        )

    def notes_for_tasks(self, task_ids, fields=None):
        """Notes on any of these tasks, oldest first.

        Chunked: a whole show's task ids in a single `in` filter makes a
        request big enough for ShotGrid to refuse.
        """
        ids = [i for i in (task_ids or []) if i]
        rows = []
        for start in range(0, len(ids), 500):
            rows += self.sg.find(
                "Note",
                [["tasks", "in", [{"type": "Task", "id": i}
                                  for i in ids[start:start + 500]]]],
                fields or config.NOTE_FIELDS,
                order=[{"field_name": "created_at", "direction": "asc"}],
            )
        return rows

    def notes_by_user(self, user, project=None, days=30):
        """Notes this artist wrote recently -- the ones replies can arrive on.

        Flow writes notes as the script user, so the artist's are the ones
        signed with their login; older ones carry them in the user field.
        """
        mine = [["user", "is", {"type": user["type"], "id": user["id"]}]]
        if self.login:
            mine += [["content", "starts_with",
                      notes_service.signer_name(self.login) + ":"],
                     ["content", "starts_with", f"[{self.login}]"]]
        filters = [
            {"filter_operator": "any", "filters": mine},
            ["created_at", "in_last", [days, "DAY"]],
        ]
        if project:
            filters.append(["project", "is",
                            {"type": "Project", "id": project["id"]}])
        return self.sg.find(
            "Note", filters, config.NOTE_FIELDS,
            order=[{"field_name": "created_at", "direction": "desc"}])

    def versions_by_user(self, user, project=None, days=30):
        """The artist's own versions in the window, for the review inbox."""
        filters = [
            ["user", "is", {"type": user["type"], "id": user["id"]}],
            ["created_at", "in_last", [days, "DAY"]],
        ]
        if project:
            filters.append(["project", "is",
                            {"type": "Project", "id": project["id"]}])
        return self.sg.find(
            "Version", filters, self._version_fields(),
            order=[{"field_name": "created_at", "direction": "desc"}])

    def replies_for_notes(self, note_ids):
        """Every reply to these notes, in one query rather than one each."""
        if not note_ids:
            return []
        return self.sg.find(
            "Reply",
            [["entity", "in",
              [{"type": "Note", "id": i} for i in note_ids]]],
            config.REPLY_FIELDS,
            order=[{"field_name": "created_at", "direction": "asc"}],
        )

    def create_note(self, project, version, content, subject="", task=None):
        """A note on a Version (or, with version=None, on the task alone).

        Written as the script user (SG_daemon): the artist is the signature
        in the text, "Jitesh: ...", not the Note's user field.
        """
        data = {
            "project": {"type": "Project", "id": project["id"]},
            "subject": subject or "",
            "content": notes_service.sign(content, self.login),
            "note_links": ([{"type": "Version", "id": version["id"]}]
                           if version else []),
        }
        entity = (task or {}).get("entity")
        if entity:
            data["note_links"].append({"type": entity["type"],
                                       "id": entity["id"]})
        if task and task.get("id"):
            data["tasks"] = [{"type": "Task", "id": task["id"]}]
        log.info("adding a note to %s", f"Version {version['id']}" if version
                 else f"Task {(task or {}).get('id')}")
        return self.sg.create("Note", data)

    def create_reply(self, note_id, content):
        data = {
            "entity": {"type": "Note", "id": note_id},
            "content": notes_service.sign(content, self.login),
        }
        log.info("replying to Note %s", note_id)
        return self.sg.create("Reply", data)

    def update_note(self, entity_type, entity_id, content):
        field = "content"
        log.info("editing %s %s", entity_type, entity_id)
        return self.sg.update(entity_type, entity_id,
                              {field: notes_service.sign(content, self.login)})

    def delete_entity(self, entity_type, entity_id):
        log.info("deleting %s %s", entity_type, entity_id)
        return self.sg.delete(entity_type, entity_id)

    def users(self, user_ids):
        """Author details, including the role ShotGrid itself assigns them.

        One query for all the authors on screen, cached for the session: a
        per-note lookup would be a query per row.
        """
        wanted = [i for i in set(user_ids or []) if i not in self._users]
        if wanted:
            try:
                found = self.sg.find(
                    "HumanUser", [["id", "in", wanted]], config.USER_FIELDS)
            except Exception as e:
                log.warning("could not read note authors: %s", e)
                found = []
            for user in found:
                self._users[user["id"]] = user
            for missing in wanted:
                self._users.setdefault(missing, None)
        return {i: self._users.get(i) for i in set(user_ids or [])}

    def publish_version(self, project, task, name, path, description="",
                        on_progress=None, work_file=""):
        """Create a Version against a task and upload the media to it.

        Two round trips, and the upload is the slow one. `on_progress` is
        called with a short message before each stage so the dialog can say
        what it is waiting on.

        `work_file` is an optional DCC scene registered after the media. It is
        deliberately last: by the time it runs the Version exists and the media
        is uploaded, so a failure there is reported on the returned dict
        (`flow_work_file_error`) rather than raised.
        """
        def say(msg):
            log.info(msg)
            if on_progress:
                on_progress(msg)

        say(f"Creating Version '{name}'…")
        version = self.sg.create(
            "Version", self._version_data(project, task, name, description))

        ext = os.path.splitext(path)[1].lower()
        is_movie = ext in config.MOVIE_EXTENSIONS

        say(f"Uploading {os.path.basename(path)}…")
        self.sg.upload("Version", version["id"], path,
                       field_name=config.VERSION_MEDIA_FIELD)

        # Movies get a thumbnail from transcoding; stills do not, so give the
        # Version something to show in list views.
        if not is_movie:
            say("Uploading thumbnail…")
            try:
                self.sg.upload_thumbnail("Version", version["id"], path)
            except Exception as e:
                # A format ShotGrid cannot make a thumbnail of (EXR, DPX) is
                # not a reason to call the publish failed.
                log.warning("thumbnail upload skipped: %s", e)

        if work_file:
            say(f"Registering {os.path.basename(work_file)}…")
            try:
                version["flow_work_file"] = self.attach_work_file(
                    project, task, version, work_file)
            except Exception as e:
                log.warning("work file not registered: %s", e)
                version["flow_work_file_error"] = str(e)

        say("Done")
        return version

    def attach_work_file(self, project, task, version, path):
        """Register the DCC scene that produced a Version.

        Returns a short sentence describing what was created, for the dialog to
        show. Raises if nothing could be registered at all -- the caller treats
        that as a warning, not a failed publish.
        """
        mode = (config.WORKFILE_MODE or "published_file").lower()
        name = os.path.basename(path)
        log.info("registering work file %s (mode=%s)", path, mode)

        if mode == "attachment":
            # field_name omitted: shotgun_api3 then links a plain Attachment to
            # the entity rather than writing a file field.
            self.sg.upload("Version", version["id"], path)
            self._record_work_path(version, path)
            return f"{name} uploaded to the Version"

        if mode == "path_only":
            self._record_work_path(version, path)
            return f"{name} recorded on the Version"

        published = self._create_published_file(project, task, version, path)
        self._record_work_path(version, path)
        return f"{name} published as PublishedFile {published['id']}"

    def _create_published_file(self, project, task, version, path):
        entity = (task or {}).get("entity")
        data = {
            "project": {"type": "Project", "id": project["id"]},
            "code": os.path.basename(path),
            config.PUBLISHED_FILE_TASK_FIELD: {"type": "Task",
                                               "id": task["id"]},
            config.PUBLISHED_FILE_VERSION_FIELD: {"type": "Version",
                                                  "id": version["id"]},
            config.PUBLISHED_FILE_PATH_FIELD: {"local_path": path},
        }
        if entity:
            data[config.PUBLISHED_FILE_ENTITY_FIELD] = {
                "type": entity["type"], "id": entity["id"]}

        file_type = self._published_file_type(path)
        if file_type:
            data["published_file_type"] = file_type

        try:
            return self.sg.create("PublishedFile", data)
        except Exception as e:
            # A local file link only resolves if a LocalStorage covers the
            # path. Without one ShotGrid rejects the whole create, and a
            # PublishedFile carrying the path as text still beats nothing.
            log.warning("PublishedFile create failed (%s); retrying without "
                        "the local path link", e)
            data.pop(config.PUBLISHED_FILE_PATH_FIELD, None)
            data["description"] = f"Work file: {path}"
            return self.sg.create("PublishedFile", data)

    def _published_file_type(self, path):
        """The site's PublishedFileType for this extension, if it has one."""
        name = config.PUBLISHED_FILE_TYPES.get(
            os.path.splitext(path)[1].lower())
        if not name:
            return None
        try:
            return self.sg.find_one("PublishedFileType",
                                    [["code", "is", name]], ["code"])
        except Exception as e:
            log.warning("could not look up PublishedFileType %r: %s", name, e)
            return None

    def _record_work_path(self, version, path):
        """Put the work file path somewhere visible on the Version itself."""
        field = config.VERSION_WORKFILE_FIELD
        if field:
            self.sg.update("Version", version["id"], {field: path})
            return
        existing = (version.get("description") or "").strip()
        note = f"Work file: {path}"
        self.sg.update("Version", version["id"],
                       {"description": f"{existing}\n{note}" if existing
                        else note})

    # -- queries -----------------------------------------------------------

    def my_tasks(self, project, statuses=None, scope=MINE):
        return self._my_tasks(project=project, statuses=statuses,
                              scope=scope)

    def all_my_tasks(self, statuses=None):
        """Every task assigned to this artist, across all projects.

        One query, for the header's search. The result is the artist's own
        workload rather than the site's, so it stays small enough to hold and
        to match against as they type.
        """
        return self._my_tasks(project=None, statuses=statuses)

    TASK_EVENTS = ["Shotgun_Task_New", "Shotgun_Task_Change",
                   "Shotgun_Task_Retirement", "Shotgun_Task_Revival"]

    def task_events_since(self, last_id):
        """(newest event id, task events after last_id, oldest first).

        last_id None only fetches the newest id, as the starting point: what
        happened before Flow opened is already in the list it loaded.
        """
        newest = [{"field_name": "id", "direction": "desc"}]
        if last_id is None:
            row = self.sg.find_one("EventLogEntry", [], ["id"], order=newest)
            return (row or {}).get("id", 0), []
        events = self.sg.find(
            "EventLogEntry",
            [["id", "greater_than", last_id],
             ["event_type", "in", self.TASK_EVENTS]],
            ["entity", "attribute_name", "event_type", "meta"],
            order=[{"field_name": "id", "direction": "asc"}], limit=500)
        return (events[-1]["id"] if events else last_id), events

    def open_tasks(self, scope=MINE):
        """Unfinished tasks across all projects, for the home page.

        The artist's own, the ones they review (scope=REVIEW), or every
        artist's on every active show (scope=EVERYONE). Filtered server-side
        so finished work never crosses the wire.
        """
        return self._my_tasks(exclude_statuses=config.TASK_DONE_STATUSES,
                              scope=scope)

    def _my_tasks(self, project=None, statuses=None, exclude_statuses=None,
                  scope=MINE):
        fields = config.TASK_FIELDS
        if scope == EVERYONE:
            filters = [] if project else \
                [["project.Project.sg_status", "is", "Active"]]
        elif scope == REVIEW:
            if not self._owner_value:
                return []
            # Asked for only here: until the site has the field, only this
            # list fails, not the artist's own.
            fields = fields + [config.TASK_LEAD_FIELD]
            filters = [[config.TASK_LEAD_FIELD, "contains", self._owner_value]]
        elif config.TASK_OWNER_IS_ENTITY:
            if not self._owner:
                return []
            filters = [[
                config.TASK_OWNER_FIELD, "is",
                {"type": self._owner["type"], "id": self._owner["id"]},
            ]]
        else:
            if not self._owner_value:
                return []
            # "contains" so a task shared by several people reaches each of
            # them; the exact match is made below.
            filters = [[config.TASK_OWNER_FIELD, "contains", self._owner_value]]
        if project:
            filters.insert(
                0, ["project", "is", {"type": "Project", "id": project["id"]}])
        if statuses:
            filters.append(["sg_status_list", "in", statuses])
        if exclude_statuses:
            filters.append(["sg_status_list", "not_in", list(exclude_statuses)])
        tasks = self.sg.find(
            "Task", filters, fields,
            order=[{"field_name": "due_date", "direction": "asc"}],
        )
        if scope == REVIEW:
            field = config.TASK_LEAD_FIELD
        elif scope == EVERYONE or config.TASK_OWNER_IS_ENTITY:
            return tasks
        else:
            field = config.TASK_OWNER_FIELD
        # "contains" also matches inside a longer name (al@ in sal@);
        # keep only the tasks that list this exact person.
        me = self._owner_value.lower()
        return [t for t in tasks if me in (
            s.strip().lower() for s in re.split(r"[,;]", t.get(field) or ""))]
