"""The Task panel: the launched task as ShotGrid sees it, inside the DCC.

    5and8 > Task Panel...

shows the task's status, artists, reviewers and due date, its newest
Versions with thumbnails, and every note on the task, on those Versions or on
the task's shot or asset -- the reviewer's RV annotations included, with
their images -- grouped by the task each was written for, with replies. The
artist can reply to a note, open a note's images, and change the task's
status with an optional note, without leaving Nuke or Maya.

Written as the ShotGrid script user, like everything Flow writes, and signed
"Jitesh: ..." with FLOW_USER so Flow and FlowDash read the artist back as the
author.

The data half (load, threads, reply, set_status) is plain Python and tested
with a fake ShotGrid; only show() needs Qt.
"""

import getpass
import os
import re
import subprocess
import tempfile
import threading

from . import context, shotgrid

TASK_FIELDS = ["content", "sg_status_list", "due_date", "entity", "step",
               "project", "sg_assigned_to", "task_reviewers"]
VERSION_FIELDS = ["code", "sg_status_list", "user", "created_by",
                  "created_at", "description", "image"]
NOTE_FIELDS = ["subject", "content", "user", "created_at", "attachments",
               "tasks", "note_links"]
REPLY_FIELDS = ["content", "user", "created_at", "entity"]
# Newest Versions shown. Their notes are the ones read, so this also bounds
# how far back the note list goes for notes not linked to the task itself.
VERSION_LIMIT = 20
# Note images, downloaded once per user and reused by every panel.
IMAGE_CACHE = os.path.join(tempfile.gettempdir(),
                           "flow_note_images_%s" % getpass.getuser())

# Same signature as Flow's notes_service: "Jitesh: ..." or the earlier
# "[jitesh] ...". The colon needs a space after it so a URL is not one.
_SIGNATURE = re.compile(
    r"^(?:\[([^\]\n]{1,64})\]|([A-Za-z][\w.\-]{0,63}):(?=\s|$))\s*")


# -- data -------------------------------------------------------------------

def signer_name(login):
    """jitesh -> Jitesh."""
    return login[:1].upper() + login[1:] if login else ""


def sign(content, login):
    """content with "Login: " in front, unless it is already signed by login."""
    if not login:
        return content
    if author_of(content, None)[0].lower() == signer_name(login).lower():
        return content
    return "%s: %s" % (signer_name(login), content)


def author_of(content, user):
    """(who wrote it, the text without the signature).

    The signature wins over the Note's user, which is the script for
    everything Flow writes.
    """
    match = _SIGNATURE.match(content or "")
    if match:
        login = (match.group(1) or match.group(2)).strip()
        return signer_name(login), (content or "")[match.end():]
    return (user or {}).get("name") or "Unknown", content or ""


def _when(value):
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value or "")[:16]


def _message(row):
    author, text = author_of(row.get("content"), row.get("user"))
    return {"id": row["id"], "author": author, "when": _when(row.get("created_at")),
            "text": text}


def group_of(note, task, version_ids=()):
    """(the task a note was written for, whether that is this task).

    A note on one of this task's Versions with no task on it (the ShotGrid
    site's media player) counts as this task's. A note on the shot or asset
    alone is grouped under its entity type.
    """
    task = task or {}
    linked = note.get("tasks") or []
    on_version = any(l.get("type") == "Version" and l.get("id") in version_ids
                     for l in note.get("note_links") or [])
    if any(t.get("id") == task.get("id") for t in linked) or             (on_version and not linked):
        return task.get("content") or "This task", True
    names = [t.get("name") for t in linked if t.get("name")]
    if names:
        return ", ".join(names), False
    return (task.get("entity") or {}).get("type") or "Shot", False


def threads(notes, replies, task=None, version_ids=()):
    """Notes in the order given, each with its replies oldest first."""
    by_note = {}
    for reply in replies:
        note_id = (reply.get("entity") or {}).get("id")
        by_note.setdefault(note_id, []).append(_message(reply))
    out = []
    for note in notes:
        thread = _message(note)
        group, own = group_of(note, task, version_ids)
        thread.update(subject=note.get("subject") or "", group=group, own=own,
                      images=list(note.get("attachments") or []),
                      replies=by_note.get(note["id"], []))
        out.append(thread)
    return out


def people(value):
    """Who a task field names: "a@x, b@x" text, an entity, or a list of them."""
    if isinstance(value, str):
        return ", ".join(p.strip() for p in re.split(r"[,;]", value) if p.strip())
    if isinstance(value, dict):
        value = [value]
    return ", ".join(e.get("name") or "" for e in value or [] if e)


def load(sg, task_id):
    """{"task", "versions", "threads"} for the panel. Four queries at most."""
    link = {"type": "Task", "id": task_id}
    task = sg.find_one("Task", [["id", "is", task_id]], TASK_FIELDS)
    if not task:
        raise LookupError("Task %s is not in ShotGrid (deleted?)." % task_id)
    versions = sg.find("Version", [["sg_task", "is", link]], VERSION_FIELDS,
                       order=[{"field_name": "created_at", "direction": "desc"}],
                       limit=VERSION_LIMIT)
    # Notes on the task; on its Versions without the task (written on the
    # ShotGrid site's media player); and on its shot or asset, which is
    # where the other tasks' notes and the shot's own are -- as in Flow.
    filters = [["tasks", "is", link]]
    entity = task.get("entity")
    if entity:
        filters.append(["note_links", "is",
                        {"type": entity["type"], "id": entity["id"]}])
    if versions:
        filters.append(["note_links", "in", [{"type": "Version", "id": v["id"]}
                                             for v in versions]])
    if len(filters) > 1:
        filters = [{"filter_operator": "any", "filters": filters}]
    notes = sg.find("Note", filters, NOTE_FIELDS,
                    order=[{"field_name": "created_at", "direction": "desc"}])
    replies = []
    if notes:
        replies = sg.find(
            "Reply", [["entity", "in", [{"type": "Note", "id": n["id"]}
                                        for n in notes]]],
            REPLY_FIELDS,
            order=[{"field_name": "created_at", "direction": "asc"}])
    return {"task": task, "versions": versions,
            "threads": threads(notes, replies, task,
                               [v["id"] for v in versions])}


def statuses(sg):
    """[(code, label)] from the site's Task status list; [] if unreadable."""
    try:
        props = sg.schema_field_read(
            "Task", "sg_status_list")["sg_status_list"]["properties"]
        codes = props["valid_values"]["value"]
        labels = (props.get("display_values") or {}).get("value") or {}
    except Exception:
        return []
    return [(c, labels.get(c, c)) for c in codes]


def reply(sg, note_id, text, login):
    text = (text or "").strip()
    if not text:
        raise ValueError("A reply needs something in it.")
    return sg.create("Reply", {"entity": {"type": "Note", "id": note_id},
                               "content": sign(text, login)})


def set_status(sg, task, code, note, login):
    """Post the note on the task first, as Flow does, then set the status."""
    note = (note or "").strip()
    if note:
        entity = task.get("entity")
        sg.create("Note", {
            "project": {"type": "Project", "id": task["project"]["id"]},
            "subject": "Set to %s" % code,
            "content": sign(note, login),
            "note_links": [{"type": entity["type"], "id": entity["id"]}]
            if entity else [],
            "tasks": [{"type": "Task", "id": task["id"]}],
        })
    return sg.update("Task", task["id"], {"sg_status_list": code})


def download_images(sg, thread, folder=None):
    """[path] of a note's attachments, each downloaded once into the note's
    own folder (IMAGE_CACHE/<note id>, or folder)."""
    folder = folder or os.path.join(IMAGE_CACHE, str(thread["id"]))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    paths = []
    for image in thread["images"]:
        name = os.path.basename(image.get("name") or "attachment_%s" % image["id"])
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            sg.download_attachment(image, file_path=path)
        paths.append(path)
    return paths


IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".bmp")


# -- Qt ---------------------------------------------------------------------

_window = None      # the open panel; kept so Qt does not collect it


def show(adapter):
    """Open the panel for this session's task, or bring it to the front."""
    global _window
    ctx = context.get()
    if not ctx.task_id:
        adapter.message("This session was launched without a task. Pick a "
                        "task in Flow and relaunch to see its notes.")
        return None
    try:
        sg = shotgrid.connect()
    except shotgrid.NotConfigured as e:
        adapter.message(str(e).replace("the publish was not registered in "
                                       "ShotGrid", "the Task panel cannot "
                                       "reach ShotGrid"))
        return None
    if _window is not None and _window.task_id == ctx.task_id:
        _window.reload()
        _window.show()
        _window.raise_()
        _window.activateWindow()
        return _window
    _window = _panel_class()(sg, ctx)
    _window.show()
    return _window


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def _panel_class():
    QtCore, QtGui, QtWidgets = _qt()

    class TaskPanel(QtWidgets.QWidget):
        def __init__(self, sg, ctx):
            QtWidgets.QWidget.__init__(
                self, QtWidgets.QApplication.activeWindow(), QtCore.Qt.Tool)
            self.sg, self.ctx, self.task_id = sg, ctx, ctx.task_id
            self.data = {"task": {}, "versions": [], "threads": []}
            self._thumbs, self._generation = {}, 0
            self.setWindowTitle("Flow Task: %s %s" % (ctx.entity_name,
                                                      ctx.task_name))
            self.resize(560, 720)
            layout = QtWidgets.QVBoxLayout(self)

            self.header = QtWidgets.QLabel()
            self.header.setWordWrap(True)
            self.header.setTextFormat(QtCore.Qt.RichText)
            layout.addWidget(self.header)

            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Status"))
            self.status = QtWidgets.QComboBox()
            for code, label in statuses(sg):
                self.status.addItem("%s (%s)" % (label, code), code)
            row.addWidget(self.status, 1)
            set_btn = QtWidgets.QPushButton("Set Status...")
            set_btn.clicked.connect(self._set_status)
            row.addWidget(set_btn)
            layout.addLayout(row)

            tabs = QtWidgets.QTabWidget()
            layout.addWidget(tabs, 1)

            notes = QtWidgets.QWidget()
            nl = QtWidgets.QVBoxLayout(notes)
            nl.setContentsMargins(0, 4, 0, 0)
            self.notes = QtWidgets.QTreeWidget()
            self.notes.setHeaderLabels(["From", "When", "Note"])
            self.notes.setAlternatingRowColors(True)
            self.notes.currentItemChanged.connect(self._show_thread)
            nl.addWidget(self.notes, 2)
            self.thread = QtWidgets.QTextBrowser()
            self.thread.setOpenExternalLinks(True)   # an image opens full size
            nl.addWidget(self.thread, 3)
            self.images_btn = QtWidgets.QPushButton("Open Images")
            self.images_btn.clicked.connect(self._open_images)
            nl.addWidget(self.images_btn)
            self.reply_box = QtWidgets.QPlainTextEdit()
            self.reply_box.setPlaceholderText("Reply to the selected note")
            self.reply_box.setMaximumHeight(80)
            nl.addWidget(self.reply_box)
            reply_btn = QtWidgets.QPushButton("Reply")
            reply_btn.clicked.connect(self._reply)
            nl.addWidget(reply_btn)
            tabs.addTab(notes, "Notes")

            self.versions = QtWidgets.QTreeWidget()
            self.versions.setHeaderLabels(["Version", "Artist", "Status",
                                           "Created"])
            self.versions.setRootIsDecorated(False)
            self.versions.setIconSize(QtCore.QSize(96, 54))
            tabs.addTab(self.versions, "Versions")

            bottom = QtWidgets.QHBoxLayout()
            self.info = QtWidgets.QLabel()
            bottom.addWidget(self.info, 1)
            refresh = QtWidgets.QPushButton("Refresh")
            refresh.clicked.connect(self.reload)
            bottom.addWidget(refresh)
            layout.addLayout(bottom)

            self.reload()

        # -- loading --------------------------------------------------------

        def _busy(self, func, *args):
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                return func(*args)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()

        def reload(self):
            # shortcut: ShotGrid is read on the UI thread (four queries, about
            # a second); move it to a thread if artists notice the pause.
            try:
                self.data = self._busy(load, self.sg, self.task_id)
            except Exception as e:
                self.info.setText("Could not read ShotGrid: %s" % e)
                return
            task = self.data["task"]
            self.header.setText(
                "<b>%s · %s</b> &nbsp; %s<br>Artists: %s<br>Reviewers: %s"
                % (_html((task.get("entity") or {}).get("name")),
                   _html(task.get("content")),
                   _html("due %s" % task["due_date"]) if task.get("due_date")
                   else "",
                   _html(people(task.get("sg_assigned_to"))) or "-",
                   _html(people(task.get("task_reviewers"))) or "-"))
            index = self.status.findData(task.get("sg_status_list"))
            if index >= 0:
                self.status.setCurrentIndex(index)
            self._fill_notes()
            self._fill_versions()
            self.info.setText("%d notes, %d versions" % (
                len(self.data["threads"]), len(self.data["versions"])))

        def _fill_notes(self):
            selected = self._current()
            self.notes.clear()
            # One branch per task, this task's first; newest note first in each.
            groups = {}
            for thread in self.data["threads"]:
                groups.setdefault((not thread["own"], thread["group"]),
                                  []).append(thread)
            bold = QtGui.QFont()
            bold.setBold(True)
            for (other, name), members in sorted(groups.items()):
                branch = QtWidgets.QTreeWidgetItem([
                    "%s%s  (%d)" % (name, "" if other else " - this task",
                                    len(members))])
                branch.setFont(0, bold)
                self.notes.addTopLevelItem(branch)
                branch.setFirstColumnSpanned(True)    # only once in the tree
                for thread in members:
                    self._add_note(branch, thread, selected)
            self.notes.expandAll()
            for column in (0, 1):
                self.notes.resizeColumnToContents(column)
            if self.notes.currentItem() is None and self.notes.topLevelItemCount():
                self.notes.setCurrentItem(self.notes.topLevelItem(0).child(0))
            self._show_thread()

        def _add_note(self, branch, thread, selected):
            first = (thread["subject"] or thread["text"]).splitlines()
            extra = []
            if thread["replies"]:
                extra.append("%d replies" % len(thread["replies"]))
            if thread["images"]:
                extra.append("%d images" % len(thread["images"]))
            item = QtWidgets.QTreeWidgetItem([
                thread["author"], thread["when"],
                (first[0] if first else "")
                + ("  [%s]" % ", ".join(extra) if extra else "")])
            item.setData(0, QtCore.Qt.UserRole, thread["id"])
            branch.addChild(item)
            if selected and thread["id"] == selected["id"]:
                self.notes.setCurrentItem(item)

        def _fill_versions(self):
            self.versions.clear()
            self._generation += 1
            urls = []
            for v in self.data["versions"]:
                who = v.get("user") or v.get("created_by") or {}
                item = QtWidgets.QTreeWidgetItem([
                    v.get("code") or "", who.get("name") or "",
                    v.get("sg_status_list") or "", _when(v.get("created_at"))])
                item.setToolTip(0, v.get("description") or "")
                self.versions.addTopLevelItem(item)
                if v.get("image"):
                    urls.append((self.versions.topLevelItemCount() - 1,
                                 v["image"]))
            for column in range(4):
                self.versions.resizeColumnToContents(column)
            self._thumbs = {}
            if urls:
                threading.Thread(target=_fetch, args=(urls, self._thumbs),
                                 daemon=True).start()
                self._apply_thumbs(self._generation, len(urls))

        def _apply_thumbs(self, generation, wanted):
            if generation != self._generation:
                return              # a reload started another fetch
            for row, data in list(self._thumbs.items()):
                item = self.versions.topLevelItem(row)
                if item is not None and data and item.icon(0).isNull():
                    pixmap = QtGui.QPixmap()
                    if pixmap.loadFromData(data):
                        item.setIcon(0, QtGui.QIcon(pixmap))
            if len(self._thumbs) < wanted:
                QtCore.QTimer.singleShot(
                    300, lambda: self._apply_thumbs(generation, wanted))

        def resizeEvent(self, event):
            QtWidgets.QWidget.resizeEvent(self, event)
            # Pictures are sized to the view: redraw them at the new width.
            QtCore.QTimer.singleShot(0, self._show_thread)

        # -- notes ----------------------------------------------------------

        def _current(self):
            item = self.notes.currentItem()
            if item is None:
                return None
            note_id = item.data(0, QtCore.Qt.UserRole)
            for thread in self.data["threads"]:
                if thread["id"] == note_id:
                    return thread
            return None

        def _show_thread(self, *args):
            thread = self._current()
            if not thread:
                self.thread.setHtml("<i>Select a note.</i>" if self.data["threads"]
                                    else "<i>No notes on this task yet.</i>")
                self.images_btn.setEnabled(False)
                return
            parts = ["<p><b>%s</b></p>" % _html(thread["subject"])
                     if thread["subject"] else ""]
            for m in [thread] + thread["replies"]:
                parts.append("<p><b>%s</b> <span style='color:gray'>%s</span>"
                             "<br>%s</p>" % (_html(m["author"]), _html(m["when"]),
                                             _html(m["text"]).replace("\n", "<br>")))
            parts += self._image_html(thread)
            self.thread.setHtml("".join(parts))
            self.images_btn.setEnabled(bool(thread["images"]))
            self.images_btn.setText("Open Images (%d)" % len(thread["images"]))

        def _image_html(self, thread):
            """The note's pictures (RV annotations) under its text.

            shortcut: downloaded on the UI thread when a note is first
            selected (an annotation PNG is about a second); thread it if
            artists notice.
            """
            if not thread["images"]:
                return []
            try:
                paths = self._busy(download_images, self.sg, thread)
            except Exception as e:
                return ["<p><i>Images not loaded: %s</i></p>" % _html(e)]
            # Room for the scroll bar the picture itself brings in.
            width = max(160, self.thread.viewport().width() - 24 -
                        self.thread.verticalScrollBar().sizeHint().width())
            out = []
            for path in paths:
                url = QtCore.QUrl.fromLocalFile(path).toString()
                if path.lower().endswith(IMAGE_TYPES):
                    out.append('<p><a href="%s"><img src="%s" width="%d"></a>'
                               "</p>" % (url, url, width))
                else:
                    out.append('<p><a href="%s">%s</a></p>'
                               % (url, _html(os.path.basename(path))))
            return out

        def _open_images(self):
            thread = self._current()
            if not thread or not thread["images"]:
                return
            try:
                folder = os.path.dirname(
                    self._busy(download_images, self.sg, thread)[0])
                subprocess.Popen(
                    [os.environ.get("FLOW_FILE_MANAGER", "xdg-open"), folder],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Flow",
                                              "Could not open the images:\n\n%s" % e)

        def _reply(self):
            thread = self._current()
            if not thread:
                return
            try:
                self._busy(reply, self.sg, thread["id"],
                           self.reply_box.toPlainText(), self.ctx.user)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Flow",
                                              "The reply was not sent:\n\n%s" % e)
                return
            self.reply_box.clear()
            self.reload()

        # -- status ---------------------------------------------------------

        def _set_status(self):
            task = self.data["task"]
            code = self.status.currentData()
            if not task or not code or code == task.get("sg_status_list"):
                return
            note, ok = QtWidgets.QInputDialog.getMultiLineText(
                self, "Set to %s" % code,
                "Note on '%s' (optional):" % (task.get("content") or ""))
            if not ok:
                return
            try:
                self._busy(set_status, self.sg, task, code, note, self.ctx.user)
            except Exception as e:
                QtWidgets.QMessageBox.warning(
                    self, "Status not changed",
                    "ShotGrid rejected the change:\n\n%s" % e)
            self.reload()

    return TaskPanel


def _fetch(urls, out):
    """Thumbnail bytes into out[row]; None for one that would not load."""
    from urllib.request import urlopen
    for row, url in urls:
        try:
            out[row] = urlopen(url, timeout=10).read()
        except Exception:
            out[row] = None


def _html(text):
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
