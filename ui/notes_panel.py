"""Notes and activity for one Version.

Reads and writes ShotGrid's own Note/Reply entities through `notes_service`;
nothing is stored locally. Refreshing notes reloads notes only -- the version
list above it is left alone, because an artist watching for a client note
should not have their selection and filters thrown away every time.
"""

import applog
import config
import notes_service

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QScrollArea, QFrame, QSizePolicy,
)

from . import jobs, theme
from .widgets import Avatar, EmptyState

log = applog.get()


def _when(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return f"{value:%d %b %H:%M}"


class MessageCard(QFrame):
    """One note or reply. Replies are indented under the note they answer."""

    reply_requested = Signal(object)
    edit_requested = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, message, can_modify, parent=None):
        super().__init__(parent)
        self.message = message
        self.setObjectName(
            "noteCardClient" if message.from_client
            else "noteCard" if message.kind == "note" else "replyCard")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(Avatar(message.author_name, 18))

        who = QLabel(message.author_name)
        who.setObjectName("tileName")
        head.addWidget(who)

        role = message.author_role
        if role:
            # ShotGrid's own permission rule set, not a role Flow invented.
            chip = QLabel(role.upper())
            chip.setObjectName(
                "roleChipClient" if message.from_client else "roleChip")
            head.addWidget(chip)

        when = QLabel(_when(message.created_at))
        when.setObjectName("tileSub")
        head.addWidget(when)
        head.addStretch()

        # On a reply too: answering it joins the same thread.
        reply = QPushButton("Reply")
        reply.setObjectName("consoleBtn")
        reply.clicked.connect(lambda: self.reply_requested.emit(message))
        head.addWidget(reply)

        if can_modify:
            # Only shown for your own messages: a button that is certain to be
            # refused by ShotGrid is worse than no button.
            edit = QPushButton("Edit")
            edit.setObjectName("consoleBtn")
            edit.clicked.connect(lambda: self.edit_requested.emit(message))
            head.addWidget(edit)

            remove = QPushButton("Delete")
            remove.setObjectName("consoleBtn")
            remove.clicked.connect(lambda: self.delete_requested.emit(message))
            head.addWidget(remove)
        lay.addLayout(head)

        if message.subject:
            subject = QLabel(message.subject)
            subject.setObjectName("noteSubject")
            subject.setWordWrap(True)
            lay.addWidget(subject)

        body = QLabel(message.content or "(empty)")
        body.setObjectName("noteBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(body)


class NotesPanel(QWidget):
    """The Notes & Activity side of the Version browser."""

    posted = Signal()
    loaded = Signal()      # notes arrived; anything showing them can redraw

    def __init__(self, sg, project, task, parent=None):
        super().__init__(parent)
        self.sg = sg
        self.service = notes_service.NotesService(sg)
        self.project = project
        self.task = task
        self.version = None
        self.threads = []
        self.replying_to = None
        self.editing = None
        self._jobs = set()
        self._request = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.title = QLabel("Notes & Activity")
        self.title.setObjectName("tileName")
        head.addWidget(self.title)
        head.addStretch()

        self.status = QLabel("")
        self.status.setObjectName("tileSub")
        head.addWidget(self.status)

        self.refresh_btn = QPushButton("↻ Refresh Notes")
        self.refresh_btn.setObjectName("consoleBtn")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self.host_lay = QVBoxLayout(self.host)
        self.host_lay.setContentsMargins(0, 0, 8, 0)
        self.host_lay.setSpacing(6)
        self.host_lay.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.host)
        lay.addWidget(self.scroll, 1)

        self.compose_label = QLabel("Add note")
        self.compose_label.setObjectName("tileSub")
        lay.addWidget(self.compose_label)

        self.compose = QPlainTextEdit()
        self.compose.setPlaceholderText("Write a note for this version…")
        self.compose.setFixedHeight(64)
        self.compose.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(self.compose)

        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_btn = QPushButton("Cancel reply")
        self.cancel_btn.setObjectName("consoleBtn")
        self.cancel_btn.clicked.connect(self._cancel_compose)
        self.cancel_btn.hide()
        actions.addWidget(self.cancel_btn)

        self.post_btn = QPushButton("Post Note")
        self.post_btn.setObjectName("termBtn")
        self.post_btn.setCursor(Qt.PointingHandCursor)
        self.post_btn.clicked.connect(self._post)
        actions.addWidget(self.post_btn)
        lay.addLayout(actions)

        self.set_version(None)

        # Notes are re-read on demand only. No polling: a client note is not
        # worth a query every few seconds from every open Flow.
        self._cooldown = QTimer(self)
        self._cooldown.setSingleShot(True)
        self._cooldown.setInterval(config.NOTES_MIN_REFRESH_SECONDS * 1000)

    # -- state -------------------------------------------------------------

    def set_version(self, version):
        self.version = version
        self._cancel_compose()
        enabled = bool(version)
        self.compose.setEnabled(enabled)
        self.post_btn.setEnabled(enabled)
        self.refresh_btn.setEnabled(enabled)
        self._clear()
        if not version:
            self.status.setText("")
            self._placeholder("Select a version to see its notes")
            return
        self.title.setText(f"Notes & Activity — {version.get('code') or ''}")
        self._cooldown.stop()           # another version is not a re-read
        self.refresh()

    def refresh(self):
        if not self.version:
            return
        if self._cooldown.isActive():
            self.status.setText("Just refreshed")
            return
        self._cooldown.start()

        self._request += 1
        request = self._request
        version = self.version
        self.status.setText("Loading notes…")

        def load():
            threads = self.service.threads(version["id"])
            return threads, self.service.activity(version, threads)

        def done(result):
            if request != self._request:
                return
            self.threads, self.activity = result
            self._render()
            self.loaded.emit()

        def failed(message):
            if request != self._request:
                return
            log.warning("could not read notes: %s", message)
            self.status.setObjectName("errorText")
            self.status.setText(f"Notes unavailable: {message}")
            self._restyle(self.status)
            self._clear()
            self._placeholder("ShotGrid did not return the notes for this "
                              "version.")

        jobs.run(self._jobs, load, done, on_error=failed)

    # -- rendering ---------------------------------------------------------

    def _clear(self):
        while self.host_lay.count():
            item = self.host_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _placeholder(self, text):
        empty = EmptyState("✎", text, "")
        self.host_lay.addWidget(empty)

    def _render(self):
        self._clear()
        self.status.setObjectName("tileSub")
        count = sum(1 + len(t.replies) for t in self.threads)
        self.status.setText(f"{count} message{'s' if count != 1 else ''}"
                            if count else "")
        self._restyle(self.status)

        if not self.threads:
            self._placeholder("No notes on this version yet")
            return

        for thread in self.threads:
            self.host_lay.addWidget(self._card(thread))
            for reply in thread.replies:
                row = QWidget()
                row_lay = QHBoxLayout(row)
                row_lay.setContentsMargins(14, 0, 0, 0)
                # The thread line: which note a reply answers is the whole
                # point of nesting it.
                rail = QFrame()
                rail.setObjectName("threadRail")
                rail.setFixedWidth(2)
                row_lay.addWidget(rail)
                row_lay.addSpacing(10)
                row_lay.addWidget(self._card(reply), 1)
                self.host_lay.addWidget(row)

    def _card(self, message):
        card = MessageCard(message, self.service.can_modify(message))
        card.reply_requested.connect(self._start_reply)
        card.edit_requested.connect(self._start_edit)
        card.delete_requested.connect(self._delete)
        return card

    # -- composing ---------------------------------------------------------

    def _start_reply(self, message):
        self.editing = None
        self.replying_to = message
        self.compose_label.setText(f"Reply to {message.author_name}")
        self.post_btn.setText("Post Reply")
        self.cancel_btn.show()
        self.compose.setFocus()

    def _start_edit(self, message):
        self.replying_to = None
        self.editing = message
        self.compose_label.setText("Edit your note")
        self.compose.setPlainText(message.content)
        self.post_btn.setText("Save")
        self.cancel_btn.show()
        self.compose.setFocus()

    def _cancel_compose(self):
        self.replying_to = None
        self.editing = None
        self.compose.clear()
        self.compose_label.setText("Add note")
        self.post_btn.setText("Post Note")
        self.cancel_btn.hide()

    def _post(self):
        text = self.compose.toPlainText().strip()
        if not text or not self.version:
            return
        editing, replying = self.editing, self.replying_to
        version, project, task = self.version, self.project, self.task
        self.post_btn.setEnabled(False)
        self.status.setText("Posting…")

        def write():
            if editing:
                return self.service.edit(editing, text)
            if replying:
                return self.service.reply(replying, text)
            return self.service.add_note(project, version, text, task=task)

        def done(_):
            self.post_btn.setEnabled(True)
            self._cancel_compose()
            self._cooldown.stop()           # the artist's own write, show it
            self.refresh()
            self.posted.emit()

        def failed(message):
            self.post_btn.setEnabled(True)
            log.warning("could not post the note: %s", message)
            self.status.setObjectName("errorText")
            self.status.setText(f"Not posted: {message}")
            self._restyle(self.status)

        jobs.run(self._jobs, write, done, on_error=failed)

    def _delete(self, message):
        self.status.setText("Deleting…")

        def remove():
            return self.service.delete(message)

        def done(_):
            self._cooldown.stop()
            self.refresh()
            self.posted.emit()

        def failed(text):
            log.warning("could not delete the note: %s", text)
            self.status.setObjectName("errorText")
            self.status.setText(f"Not deleted: {text}")
            self._restyle(self.status)

        jobs.run(self._jobs, remove, done, on_error=failed)

    @staticmethod
    def _restyle(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class ActivityPanel(QWidget):
    """Notes, replies and the publish itself on one timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self.host_lay = QVBoxLayout(self.host)
        self.host_lay.setAlignment(Qt.AlignTop)
        self.host_lay.setSpacing(4)
        self.scroll.setWidget(self.host)
        lay.addWidget(self.scroll)

    def show_events(self, events):
        while self.host_lay.count():
            item = self.host_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not events:
            self.host_lay.addWidget(EmptyState("◷", "No activity yet", ""))
            return

        for event in events:
            row = QLabel(f"{_when(event['when'])}   ·   {event['who']} "
                         f"{_verb(event)} {event['text']}")
            row.setObjectName("tileSub" if event["kind"] != "publish"
                              else "tileName")
            row.setWordWrap(True)
            self.host_lay.addWidget(row)


def _verb(event):
    return {"publish": "", "note": "wrote:", "reply": "replied:"}.get(
        event["kind"], "")
