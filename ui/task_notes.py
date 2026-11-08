"""A task's notes as a chat, opened from the home page's Notes column.

The same ShotGrid Notes and Replies the version browser shows -- anything
linked to the task or to its shot (or asset), oldest at the top -- drawn as
message bubbles: yours on the right, everyone else's on the left, each named
by the login it was signed with (see notes_service.sign). A note that came
from the shot rather than this task is tagged with where it came from, and
the chips at the top narrow the chat to the task or the shot. A note's
pictures -- a reviewer's RV annotations -- show in its bubble; click one to
see it full screen. Posting writes a new Note on the task.
"""

import datetime
import getpass
import os
import tempfile

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import applog
import notes_service

from . import jobs
from .widgets import Avatar, EmptyState

log = applog.get()


ALL, TASK, SHOT = "all", "task", "shot"

IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".bmp")
# Note pictures, downloaded once per user and kept for the next look.
IMAGE_CACHE = os.path.join(tempfile.gettempdir(),
                           f"flow_note_images_{getpass.getuser()}")


def images_of(message):
    """The picture attachments of a note; replies cannot carry any."""
    return [a for a in (message.raw.get("attachments") or [])
            if (a.get("name") or "").lower().endswith(IMAGE_TYPES)]


def download_images(client, note_id, attachments, folder=None):
    """[path] of these attachments, each downloaded once into the note's own
    folder under IMAGE_CACHE."""
    folder = folder or os.path.join(IMAGE_CACHE, str(note_id))
    os.makedirs(folder, exist_ok=True)
    paths = []
    for attachment in attachments:
        path = os.path.join(folder, os.path.basename(
            attachment.get("name") or f"attachment_{attachment['id']}"))
        if not os.path.isfile(path):
            client.download_attachment(attachment, path)
        paths.append(path)
    return paths


class PictureViewer(QDialog):
    """A note's pictures full screen. Esc or a click closes; the arrow keys
    step through the note's other pictures."""

    def __init__(self, paths, index=0, parent=None):
        super().__init__(parent)
        self.paths, self.index = paths, index
        self.setWindowTitle("Note picture")
        self.setStyleSheet("background: black; color: #e6e8eb;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(1, 1)       # or the pixmap pins the window size
        lay.addWidget(self.image, 1)
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.caption)
        self._show()

    def _show(self):
        path = self.paths[self.index]
        self.pixmap = QPixmap(path)
        count = (f"  ({self.index + 1} of {len(self.paths)})"
                 if len(self.paths) > 1 else "")
        self.caption.setText(f"{os.path.basename(path)}{count}   ·   "
                             + ("← → for the others   ·   " if count else "")
                             + "Esc or click to close")
        self._fit()

    def _fit(self):
        if self.pixmap.isNull():
            self.image.setText("This picture could not be read.")
            return
        self.image.setPixmap(self.pixmap.scaled(
            self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def step(self, delta):
        self.index = (self.index + delta) % len(self.paths)
        self._show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Right, Qt.Key_Space, Qt.Key_Down):
            self.step(1)
        elif event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.step(-1)
        else:
            super().keyPressEvent(event)      # Esc closes

    def mouseReleaseEvent(self, event):
        self.close()


class Picture(QLabel):
    """A note's image, scaled to the bubble; a click shows it full screen."""

    def __init__(self, path, paths=None, index=0, width=480):
        super().__init__()
        self.path, self.paths, self.index = path, paths or [path], index
        self.viewer = None
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.setText(os.path.basename(path))
        else:
            self.setPixmap(pixmap.scaledToWidth(
                min(width, pixmap.width()), Qt.SmoothTransformation))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{os.path.basename(path)} (click for full screen)")

    def mouseReleaseEvent(self, event):
        self.viewer = PictureViewer(self.paths, self.index, self.window())
        self.viewer.setAttribute(Qt.WA_DeleteOnClose)
        self.viewer.showFullScreen()


def _when(value):
    if not value:
        return ""
    return value if isinstance(value, str) else f"{value:%H:%M}"


def _day(value):
    """Today / Yesterday / Mon 12 Oct, for the divider above a day's notes;
    "" when ShotGrid gave no date to group by."""
    if not isinstance(value, datetime.datetime):
        return ""
    today = datetime.date.today()
    day = value.date()
    if day == today:
        return "Today"
    if day == today - datetime.timedelta(days=1):
        return "Yesterday"
    return f"{value:%a %d %b}" + ("" if day.year == today.year
                                  else f" {day.year}")


class Bubble(QWidget):
    """One message, pushed to the right when it is the viewer's own."""

    def __init__(self, message, mine, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        card = QFrame()
        card.setObjectName("chatMine" if mine else
                           "chatClient" if message.from_client
                           else "chatTheirs")
        card.setMaximumWidth(520)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(6)
        meta = QLabel(("You" if mine else message.author_name)
                      + ("  ·  CLIENT" if message.from_client and not mine
                         else "")
                      + ("  ·  reply" if message.kind == "reply" else "")
                      + f"  ·  {_when(message.created_at)}")
        meta.setObjectName("chatMeta")
        top.addWidget(meta)
        top.addStretch()
        if message.scope == SHOT:
            where = QLabel(message.where or "Shot")
            where.setObjectName("scopeChip")
            where.setToolTip("Written on the shot, not on this task")
            top.addWidget(where)
        lay.addLayout(top)

        if message.subject:
            subject = QLabel(message.subject)
            subject.setObjectName("noteSubject")
            subject.setWordWrap(True)
            lay.addWidget(subject)

        body = QLabel(message.content or "(empty)")
        body.setObjectName("chatBody")
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # A wrapping label asks for almost no width, so a short message stays
        # on one line and a long one gets the bubble's full width to wrap in.
        if len(body.text()) > 60 or "\n" in body.text():
            body.setWordWrap(True)
            card.setMinimumWidth(420)
        lay.addWidget(body)
        self.card, self.card_lay = card, lay

        if mine:
            row.addStretch()
            row.addWidget(card)
        else:
            row.addWidget(Avatar(message.author_name, 24), 0, Qt.AlignTop)
            row.addWidget(card)
            row.addStretch()


class TaskNotesDialog(QDialog):
    posted = Signal()

    def __init__(self, sg, project, task, parent=None):
        super().__init__(parent)
        self.service = notes_service.NotesService(sg)
        self.client = sg
        self._pictures = {}           # note id -> [path], once downloaded
        self.project = project
        self.task = task
        self.messages = []
        self.filter = ALL
        self._jobs = set()

        entity = (task.get("entity") or {}).get("name") or ""
        self.setWindowTitle(f"Notes — {entity} {task.get('content', '')}"
                            .replace("  ", " "))
        self.resize(600, 680)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel(" · ".join(x for x in (
            entity, task.get("content") or "",
            (task.get("project") or {}).get("name") or "") if x))
        title.setObjectName("tileName")
        head.addWidget(title)
        head.addStretch()
        self.status = QLabel("")
        self.status.setObjectName("tileSub")
        head.addWidget(self.status)
        refresh = QPushButton("↻")
        refresh.setObjectName("consoleBtn")
        refresh.setToolTip("Reload the notes from ShotGrid")
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        lay.addLayout(head)

        # All / This task / Shot. Without a shot or asset there is nothing
        # but the task, so the chips would only be noise.
        self._chip_text = {ALL: "All", TASK: "This task", SHOT:
                           (task.get("entity") or {}).get("type") or "Shot"}
        chips = QHBoxLayout()
        chips.setSpacing(6)
        group = QButtonGroup(self)
        self.filter_buttons = {}
        for key, text in self._chip_text.items():
            btn = QPushButton(text)
            btn.setObjectName("chip")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            group.addButton(btn)
            chips.addWidget(btn)
            self.filter_buttons[key] = btn
        chips.addStretch()
        self.filter_buttons[ALL].setChecked(True)
        self.chips = QWidget()
        self.chips.setLayout(chips)
        self.chips.setVisible(bool(task.get("entity")))
        lay.addWidget(self.chips)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self.host_lay = QVBoxLayout(self.host)
        self.host_lay.setContentsMargins(0, 0, 8, 0)
        self.host_lay.setSpacing(10)
        self.host_lay.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.host)
        lay.addWidget(self.scroll, 1)

        compose = QHBoxLayout()
        self.compose = QPlainTextEdit()
        self.compose.setPlaceholderText(
            "Write a note on this task…  (Ctrl+Enter to send)")
        self.compose.setFixedHeight(64)
        compose.addWidget(self.compose, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("termBtn")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self.send)
        compose.addWidget(self.send_btn, 0, Qt.AlignBottom)
        lay.addLayout(compose)

        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self.compose, activated=self.send)

        self.refresh()
        self.compose.setFocus()

    # -- reading -----------------------------------------------------------

    def refresh(self):
        self.status.setText("Loading…")
        jobs.run(self._jobs, self.service.task_chat, self._show,
                 self.task, on_error=self._failed)

    def set_filter(self, key):
        self.filter = key
        self.filter_buttons[key].setChecked(True)
        self._draw()

    def _show(self, messages):
        self.messages = messages
        counts = {ALL: len(messages),
                  TASK: sum(m.scope == TASK for m in messages),
                  SHOT: sum(m.scope == SHOT for m in messages)}
        for key, text in self._chip_text.items():
            self.filter_buttons[key].setText(
                f"{text}  {counts[key]}" if counts[key] else text)
        self.status.setText(f"{len(messages)} message"
                            f"{'s' if len(messages) != 1 else ''}"
                            if messages else "")
        self._draw()

    def _draw(self):
        while self.host_lay.count():
            item = self.host_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        shown = [m for m in self.messages
                 if self.filter == ALL or m.scope == self.filter]
        if not shown:
            title, hint = {
                ALL: ("No notes yet",
                      "Notes on this task and its shot show here. Anything "
                      "you write is saved to ShotGrid with your login on it."),
                TASK: ("No notes on this task yet",
                       "Anything you write here is saved to ShotGrid with "
                       "your login on it."),
                SHOT: ("No notes from the shot",
                       "Notes written on the shot, or for its other tasks, "
                       "show here."),
            }[self.filter]
            self.host_lay.addWidget(EmptyState("✎", title, hint))
            return
        day = None
        for message in shown:
            label = _day(message.created_at)
            if label and label != day:
                day = label
                divider = QLabel(label)
                divider.setObjectName("dayDivider")
                divider.setAlignment(Qt.AlignCenter)
                self.host_lay.addWidget(divider)
            bubble = Bubble(message, self.service.can_modify(message))
            self.host_lay.addWidget(bubble)
            if message.kind == "note" and images_of(message):
                self._show_pictures(bubble, message)
        # After layout, or the maximum is still the old, shorter one.
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def _show_pictures(self, bubble, message):
        """Into the bubble now if downloaded before, else once they are."""
        def add(paths):
            self._pictures[message.id] = paths
            try:
                for i, path in enumerate(paths):
                    bubble.card_lay.addWidget(Picture(path, paths, i))
                bubble.card.setMinimumWidth(420)
            except RuntimeError:
                pass                  # redrawn meanwhile; the next draw has them

        if message.id in self._pictures:
            add(self._pictures[message.id])
            return
        jobs.run(self._jobs, download_images, add, self.client, message.id,
                 images_of(message), on_error=lambda e: log.warning(
                     "note %s images not loaded: %s", message.id, e))

    def _failed(self, message):
        log.warning("could not read the task's notes: %s", message)
        self.status.setText(f"Notes unavailable: {message}")

    # -- writing -----------------------------------------------------------

    def send(self):
        text = self.compose.toPlainText().strip()
        if not text or not self.send_btn.isEnabled():
            return
        self.send_btn.setEnabled(False)
        self.status.setText("Sending…")

        def done(_):
            self.send_btn.setEnabled(True)
            self.compose.clear()
            self.posted.emit()
            self.refresh()

        def failed(message):
            self.send_btn.setEnabled(True)
            log.warning("could not post the task note: %s", message)
            self.status.setText(f"Not sent: {message}")

        jobs.run(self._jobs, self.service.add_note, done,
                 self.project, None, text, task=self.task, on_error=failed)
