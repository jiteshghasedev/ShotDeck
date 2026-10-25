"""Needs Attention: what is waiting on this artist, and a way straight to it.

Every row knows its project, entity, task, version and note, so opening one
lands on the version rather than telling the artist where to go and look. The
list is built by `review_service` from ShotGrid data; this file only draws it.

Loaded on demand and on an explicit Refresh. No polling.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame, QStackedWidget,
)

import applog
import config
import media_cache
import review_service
from . import jobs, theme
from .widgets import EmptyState, flash_refreshed

log = applog.get()


def _when(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    import datetime
    now = datetime.datetime.now(value.tzinfo) if value.tzinfo else \
        datetime.datetime.now()
    seconds = (now - value).total_seconds()
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    if (now.date() - value.date()).days == 1:
        return "Yesterday"
    return f"{value:%d %b %H:%M}"


class ReviewCard(QFrame):
    """One item. Clicking it opens the version it is about."""

    opened = Signal(object)
    compare_requested = Signal(object)

    def __init__(self, item, unread, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("noteCard" if unread else "replyCard")
        self.setCursor(Qt.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)

        head = QHBoxLayout()
        dot = QLabel("●")
        # Red for something waiting on you, amber for something you should
        # know about, grey once it has been opened.
        dot.setObjectName("checkError" if (unread and item.requires_action)
                          else ("checkWarn" if unread else "tileSub"))
        head.addWidget(dot)

        title = QLabel(item.headline())
        title.setObjectName("tileName")
        head.addWidget(title)
        head.addStretch()

        when = QLabel(_when(item.created_at))
        when.setObjectName("tileSub")
        head.addWidget(when)
        lay.addLayout(head)

        where = QLabel(item.where)
        where.setObjectName("tileSub")
        lay.addWidget(where)

        if item.text:
            body = QLabel(f"“{item.text[:160]}”")
            body.setObjectName("noteBody")
            body.setWordWrap(True)
            lay.addWidget(body)

        actions = QHBoxLayout()
        actions.addStretch()
        open_btn = QPushButton("Open Version")
        open_btn.setObjectName("consoleBtn")
        open_btn.clicked.connect(lambda: self.opened.emit(item))
        actions.addWidget(open_btn)

        compare = QPushButton("Compare with previous")
        compare.setObjectName("consoleBtn")
        compare.setEnabled(bool(item.version.get("id")))
        compare.clicked.connect(lambda: self.compare_requested.emit(item))
        actions.addWidget(compare)
        lay.addLayout(actions)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.opened.emit(self.item)
        super().mouseReleaseEvent(event)


class ReviewPage(QWidget):
    """The Needs Attention list."""

    item_opened = Signal(object)
    compare_requested = Signal(object)
    count_changed = Signal(int)

    def __init__(self, sg, parent=None):
        super().__init__(parent)
        self.sg = sg
        self.service = review_service.ReviewService(sg)
        self.items = []
        self.project = None
        self._jobs = set()
        self._request = 0
        self._prefetching = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("Needs Attention")
        title.setObjectName("headerTitle")
        head.addWidget(title)

        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("tileSub")
        head.addWidget(self.count_lbl)
        head.addStretch()

        self.read_all_btn = QPushButton("Mark all read")
        self.read_all_btn.setObjectName("consoleBtn")
        self.read_all_btn.clicked.connect(self._mark_all_read)
        head.addWidget(self.read_all_btn)

        self.refresh_btn = QPushButton("↻ Refresh")
        self.refresh_btn.setObjectName("termBtn")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(lambda: self.refresh(clicked=True))
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self.host_lay = QVBoxLayout(self.host)
        self.host_lay.setAlignment(Qt.AlignTop)
        self.host_lay.setSpacing(8)
        self.scroll.setWidget(self.host)
        self.stack.addWidget(self.scroll)

        self.empty = EmptyState(
            "✓", "Nothing needs your attention",
            "New notes on your versions, replies to your notes, and versions "
            "a supervisor pushed back all show up here.")
        self.stack.addWidget(self.empty)

        self.loading = EmptyState("◔", "Looking for review activity…", "")
        self.stack.addWidget(self.loading)
        self.stack.setCurrentWidget(self.loading)

    # -- loading -----------------------------------------------------------

    def set_project(self, project):
        """Scope to one project, or None for everything the artist has."""
        self.project = project

    def refresh(self, clicked=False):
        self._request += 1
        request = self._request
        project = self.project
        self.stack.setCurrentWidget(self.loading)

        def load():
            return self.service.needs_attention(project=project)

        def done(items):
            if request != self._request:
                return
            self.items = items
            self.service.read_state.prune(items)
            self._render()
            self._prefetch(items)
            if clicked:
                flash_refreshed(self.refresh_btn)

        def failed(message):
            if request != self._request:
                return
            log.warning("could not build the review inbox: %s", message)
            self.count_lbl.setObjectName("errorText")
            self.count_lbl.setText(f"Review activity unavailable: {message}")
            self.count_lbl.style().unpolish(self.count_lbl)
            self.count_lbl.style().polish(self.count_lbl)
            self.stack.setCurrentWidget(self.empty)

        jobs.run(self._jobs, load, done, on_error=failed)

    def _render(self):
        while self.host_lay.count():
            item = self.host_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        unread = self.service.unread(self.items)
        self.count_lbl.setObjectName("tileSub")
        self.count_lbl.setText(
            f"{len(self.items)} item{'s' if len(self.items) != 1 else ''}"
            + (f", {len(unread)} unread" if unread else ""))
        self.count_lbl.style().unpolish(self.count_lbl)
        self.count_lbl.style().polish(self.count_lbl)
        self.count_changed.emit(len(unread))

        if not self.items:
            self.stack.setCurrentWidget(self.empty)
            return

        for item in self.items:
            card = ReviewCard(item, not self.service.read_state.is_read(item))
            card.opened.connect(self._open)
            card.compare_requested.connect(self.compare_requested)
            self.host_lay.addWidget(card)
        self.stack.setCurrentWidget(self.scroll)

    # -- actions -----------------------------------------------------------

    def _prefetch(self, items):
        """Download the inbox's movies now, so opening one plays from disk.

        One run at a time: a refresh while one is going adds nothing it would
        not already fetch next time.
        """
        if self._prefetching or config.REVIEW_PREFETCH <= 0:
            return
        # Newest first, each version once, however many items point at it.
        versions = list({i.version["id"]: i.version for i in items
                         if (i.version or {}).get("id")}.values())
        if not versions:
            return
        self._prefetching = True

        def finished(count):
            self._prefetching = False
            if count:
                log.info("review inbox: %s movie%s cached ahead", count,
                         "" if count == 1 else "s")

        def failed(message):
            self._prefetching = False
            log.info("review inbox prefetch stopped: %s", message)

        jobs.run(self._jobs, media_cache.prefetch, finished, versions,
                 config.REVIEW_PREFETCH, on_error=failed)

    def _open(self, item):
        self.service.mark_read(item)
        self._render()
        self.item_opened.emit(item)

    def _mark_all_read(self):
        self.service.read_state.mark_all_read(self.items)
        self._render()

    def attention_by_task(self):
        return self.service.attention_by_task(self.items)
