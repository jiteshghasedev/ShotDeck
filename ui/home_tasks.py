"""The artist's open tasks, on the home page, across every project.

The first question an artist has when Flow opens is "what am I working on".
This answers it without picking a project first:

- every row has a launch button that opens the app last used on that task
  (its dropdown lists every DCC, the same entries as the right-click menu);
- double-click opens the task in its project, the same place the header search
  goes; right-click gives the project page's task menu (TaskMenu), acting on
  the task's own project, so there is no second copy of those actions;
- one row of chips filters by due date or status, with live counts;
- the Notes column counts the task's ShotGrid notes and dates the newest
  (sortable); clicking it opens the task's notes as a chat.

The Mine / To Review / Everyone switch shows instead the tasks that list you
in config.TASK_LEAD_FIELD (Task.task_reviewers), or every artist's on every
active show -- both
with an Artist column and filter. Production starts on Everyone.

"Open" means any status not in config.TASK_DONE_STATUSES. That filter runs on
the server, so finished work never crosses the wire. The filters on this page
run in memory over the list that came back.
"""

import datetime
import re

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QHBoxLayout,
    QLabel, QMenu, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

import config
from sg_client import EVERYONE, MINE, REVIEW
from . import theme, ui_state
from .branding import LoadingPage
from .software_page import TaskMenu
from .widgets import (
    DueDate, EmptyState, StatusPill, flash_refreshed, load_thumbnail, names,
    resizable,
)

ALL = "all"
OVERDUE = "overdue"
WEEK = "week"
STATUS = "status:"          # prefix: "status:ip" keeps only that status

WEEK_DAYS = 7


def _due(task, field="due_date"):
    try:
        return datetime.date.fromisoformat(task.get(field) or "")
    except ValueError:
        return None


def start_label(task, today=None):
    """"Thu 2 Oct" -- or "" with no start date."""
    start = _due(task, "start_date")
    if start is None:
        return ""
    today = today or datetime.date.today()
    text = f"{start:%a} {start.day} {start:%b}"
    return text if start.year == today.year else f"{text} {start.year}"


def chip_matches(task, chip, today):
    if chip == OVERDUE:
        due = _due(task)
        return due is not None and due < today
    if chip == WEEK:
        due = _due(task)
        return due is not None and \
            today <= due <= today + datetime.timedelta(days=WEEK_DAYS)
    if chip.startswith(STATUS):
        return task.get("sg_status_list") == chip[len(STATUS):]
    return True


def filter_tasks(tasks, project_id=None, chip=ALL, today=None):
    """The tasks in this project (None: every project) that pass the chip."""
    today = today or datetime.date.today()
    return [t for t in tasks
            if (project_id is None or
                (t.get("project") or {}).get("id") == project_id)
            and chip_matches(t, chip, today)]


def due_label(task, today=None):
    """"2 days late", "Today", "Tomorrow", "Thu 2 Oct" -- or "" with no date."""
    due = _due(task)
    if due is None:
        return ""
    today = today or datetime.date.today()
    days = (due - today).days
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} late"
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    text = f"{due:%a} {due.day} {due:%b}"
    return text if due.year == today.year else f"{text} {due.year}"


class HomeTasks(TaskMenu, QWidget):
    COLS = ["Task", "Shot / Asset", "Project", "Step", "Artist", "Reviewer",
            "Status", "Start", "Due", "Notes", ""]
    COL_ARTIST = 4
    COL_REVIEWER = 5
    COL_STATUS = 6
    COL_START = 7
    COL_DUE = 8
    COL_NOTES = 9
    COL_LAUNCH = 10

    task_opened = Signal(object)          # the Task dict
    notes_requested = Signal(object)      # the Task dict, from the Notes cell
    refresh_requested = Signal()
    scope_changed = Signal(str)           # MINE, REVIEW or EVERYONE
    # The task menu's actions, as on the project page's TasksTable.
    package_launched = Signal(object, str, str)
    folder_requested = Signal(str)
    latest_version_requested = Signal(object, object)
    status_change_requested = Signal(object, str)
    publish_requested = Signal(object)
    versions_requested = Signal(object)

    def __init__(self, production=False):
        super().__init__()
        # Whose open tasks: your own, the ones you lead or review, or every
        # artist's. Anyone can switch; production starts on everyone's.
        self.scope = EVERYONE if production else MINE
        self._tasks = []
        self._rows = []
        self._status_labels = {}
        self._loading = False
        self._chip = ALL
        self._sort = (self.COL_DUE, Qt.AscendingOrder)
        self._last_launch = ui_state.get("last_launch", {})
        self._projects = {}          # id -> full Project dict, for folders
        self._statuses = []          # [(code, label), ...] for TaskMenu
        self._latest = {}            # task id -> newest Version
        self._attention = {}         # the review dots are project-page only
        self._packages = []
        self._notes = {}             # task id -> (count, newest created_at)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 8)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setObjectName("headerTitle")
        top.addWidget(self.heading)
        top.addSpacing(12)
        scope = QButtonGroup(self)
        self.scope_buttons = {}
        for key, text in ((MINE, "Mine"), (REVIEW, "To Review"),
                          (EVERYONE, "Everyone")):
            btn = QPushButton(text)
            btn.setObjectName("chip")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.set_scope(k))
            scope.addButton(btn)
            top.addWidget(btn)
            self.scope_buttons[key] = btn
        top.addStretch()
        # Everyone only: one artist's tasks, for a manager checking a load.
        self.artist_box = QComboBox()
        self.artist_box.setMinimumWidth(150)
        top.addWidget(self.artist_box)
        self.project_box = QComboBox()
        self.project_box.setMinimumWidth(150)
        top.addWidget(self.project_box)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("termBtn")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        top.addWidget(self.refresh_btn)
        lay.addLayout(top)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        self._chip_buttons = {}          # key -> button
        self._chip_row = chips
        for key in (ALL, OVERDUE, WEEK):
            self._add_chip(key)
        self._status_chip_at = chips.count()
        chips.addStretch()
        hint = QLabel("Double-click to open · right-click for more · "
                      "click Notes to chat · Ctrl+scroll for row height")
        hint.setObjectName("tileSub")
        chips.addWidget(hint)
        lay.addLayout(chips)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(*self._sort)
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        resizable(self.table, "home_tasks_table",
                  [280, 130, 130, 100, 140, 140, 150, 110, 110, 140, 150])
        self.table.setIconSize(QSize(56, 32))
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setMouseTracking(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setItemDelegateForColumn(self.COL_STATUS,
                                            StatusPill(self.table))
        self.table.setItemDelegateForColumn(self.COL_DUE, DueDate(self.table))
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.stack.addWidget(self.table)

        self.caught_up = EmptyState(
            "✓", "Nothing open — you're all caught up",
            "Tasks assigned to you that aren't finished show up here.")
        self.stack.addWidget(self.caught_up)

        self.filtered_empty = QWidget()
        fe = QVBoxLayout(self.filtered_empty)
        fe.setAlignment(Qt.AlignCenter)
        fe.setSpacing(10)
        self.filtered_title = QLabel("")
        self.filtered_title.setObjectName("emptyTitle")
        self.filtered_title.setAlignment(Qt.AlignCenter)
        fe.addWidget(self.filtered_title)
        clear = QPushButton("Clear filters")
        clear.setObjectName("termBtn")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clear_filters)
        fe.addWidget(clear, 0, Qt.AlignCenter)
        self.stack.addWidget(self.filtered_empty)

        self.loading = LoadingPage("Loading your tasks...")
        self.stack.addWidget(self.loading)

        # Connected last, so filling the box never rebuilds a missing table.
        self.project_box.currentIndexChanged.connect(self._rebuild)
        self.artist_box.currentIndexChanged.connect(self._rebuild)
        self.set_scope(self.scope, reload=False)

    # -- scope ----------------------------------------------------------------

    HEADINGS = {MINE: "My Open Tasks", REVIEW: "Tasks To Review",
                EVERYONE: "All Open Tasks"}

    def set_scope(self, scope, reload=True):
        """Mine, To Review or Everyone. The window reloads on scope_changed."""
        changed = scope != self.scope
        self.scope = scope
        self.heading.setText(self.HEADINGS[scope])
        self.scope_buttons[scope].setChecked(True)
        # Whose task it is only matters when it is not always your own.
        theirs = scope != MINE
        self.table.setColumnHidden(self.COL_ARTIST, not theirs)
        self.artist_box.setVisible(theirs)
        if changed and reload:
            self._tasks = []
            self.scope_changed.emit(scope)

    # -- chips ----------------------------------------------------------------

    def _add_chip(self, key, at=None):
        btn = QPushButton()
        btn.setObjectName("chip")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _=False, k=key: self._set_chip(k))
        self.chip_group.addButton(btn)
        if at is None:
            self._chip_row.addWidget(btn)
        else:
            self._chip_row.insertWidget(at, btn)
        self._chip_buttons[key] = btn
        return btn

    def _chip_label(self, key):
        if key == ALL:
            return "All"
        if key == OVERDUE:
            return "Overdue"
        if key == WEEK:
            return "Due this week"
        code = key[len(STATUS):]
        return self._status_labels.get(code, code)

    def _refresh_chips(self, in_project):
        """One chip per status the artist has, and live counts on all of them."""
        counts = {}
        for t in in_project:
            code = t.get("sg_status_list")
            if code:
                counts[code] = counts.get(code, 0) + 1
        wanted = [STATUS + c for c in
                  sorted(counts, key=lambda c: (-counts[c],
                                                self._chip_label(STATUS + c)))]

        for key in [k for k in self._chip_buttons if k.startswith(STATUS)]:
            if key not in wanted:
                btn = self._chip_buttons.pop(key)
                self.chip_group.removeButton(btn)
                btn.deleteLater()
        for i, key in enumerate(wanted):
            btn = self._chip_buttons.get(key) or self._add_chip(key)
            self._chip_row.removeWidget(btn)
            self._chip_row.insertWidget(self._status_chip_at + i, btn)

        if self._chip not in self._chip_buttons:
            self._chip = ALL
        today = datetime.date.today()
        for key, btn in self._chip_buttons.items():
            n = sum(1 for t in in_project if chip_matches(t, key, today))
            btn.setText(f"{self._chip_label(key)}  {n}")
            btn.setChecked(key == self._chip)

    def _set_chip(self, key):
        self._chip = key
        self._rebuild()

    def clear_filters(self):
        self._chip = ALL
        self.artist_box.setCurrentIndex(0)      # each rebuilds via the signal
        self.project_box.setCurrentIndex(0)
        self._rebuild()

    # -- data ---------------------------------------------------------------

    def _on_refresh_clicked(self):
        self._refresh_clicked = True
        self.refresh_requested.emit()

    def set_loading(self):
        self._loading = True
        if not self._tasks:
            self.stack.setCurrentWidget(self.loading)

    def set_statuses(self, statuses):
        self._statuses = statuses or []
        self._status_labels = dict(self._statuses)
        self._rebuild()

    def set_projects(self, projects):
        self._projects = {p["id"]: p for p in projects or []}

    def _menu_project(self, task):
        """The task's full project: folder paths need its tank_name."""
        link = task.get("project") or {}
        return self._projects.get(link.get("id")) or (link or None)

    def update_task(self, task_id, code):
        """A status write from the menu. Finished tasks leave the list."""
        if code in config.TASK_DONE_STATUSES:
            self._tasks = [t for t in self._tasks if t["id"] != task_id]
            self._rebuild()
            return
        super().update_task(task_id, code)

    def set_tasks(self, tasks, failed=False):
        self._tasks = tasks
        self._loading = False
        if getattr(self, "_refresh_clicked", False):
            self._refresh_clicked = False
            if not failed:
                flash_refreshed(self.refresh_btn)
        projects = {}
        for t in self._tasks:
            p = t.get("project") or {}
            if p.get("id") is not None:
                projects[p["id"]] = p.get("name") or str(p["id"])
        self._fill_box(self.project_box, "All projects", projects)
        artists = {a: a for t in self._tasks for a in self._artists(t)}
        self._fill_box(self.artist_box, "All artists", artists)
        self._rebuild()

    @staticmethod
    def _fill_box(box, everything, names):
        """Offer only what the tasks have, {data: name}, keeping the pick."""
        current = box.currentData()
        box.blockSignals(True)
        box.clear()
        box.addItem(everything, None)
        for data, name in sorted(names.items(), key=lambda x: x[1].lower()):
            box.addItem(name, data)
        index = box.findData(current)
        box.setCurrentIndex(index if index >= 0 else 0)
        box.blockSignals(False)

    def set_notes(self, summary):
        """{task id: (count, newest created_at)} from NotesService."""
        self._notes = summary or {}
        self._rebuild()

    def remember_launch(self, task, package, version):
        """The app a task was last opened in, for its launch button."""
        self._last_launch[str(task["id"])] = [package, version]
        ui_state.put("last_launch", self._last_launch)
        self._rebuild()

    # -- sorting ------------------------------------------------------------

    def _on_header_clicked(self, col):
        if col == self.COL_LAUNCH:
            self.table.horizontalHeader().setSortIndicator(*self._sort)
            return
        col_now, order = self._sort
        if col == col_now:
            order = Qt.DescendingOrder if order == Qt.AscendingOrder \
                else Qt.AscendingOrder
        else:
            # Notes: the latest conversation first is the useful first click.
            order = Qt.DescendingOrder if col == self.COL_NOTES \
                else Qt.AscendingOrder
        self._sort = (col, order)
        self.table.horizontalHeader().setSortIndicator(col, order)
        self._rebuild()

    def _sort_key(self, task, col):
        if col == self.COL_DUE:
            return _due(task)
        if col == self.COL_START:
            return _due(task, "start_date")
        if col == self.COL_STATUS:
            code = task.get("sg_status_list") or ""
            return self._status_labels.get(code, code).lower()
        if col == self.COL_NOTES:
            return self._notes.get(task["id"], (0, None))[1]
        return str(self._values(task)[col]).lower()

    def _sorted(self, rows):
        """Sorted on the chosen column; tasks with no due date always last."""
        col, order = self._sort
        dated = [t for t in rows if self._sort_key(t, col) is not None]
        undated = [t for t in rows if self._sort_key(t, col) is None]
        dated.sort(key=lambda t: self._sort_key(t, col),
                   reverse=order == Qt.DescendingOrder)
        return dated + undated

    # -- view ---------------------------------------------------------------

    @staticmethod
    def _artist(task):
        owner = task.get(config.TASK_OWNER_FIELD) or ""
        return owner.get("name", "") if isinstance(owner, dict) else owner

    @classmethod
    def _artists(cls, task):
        """Each person a shared task names, for the artist filter."""
        return [a.strip() for a in re.split(r"[,;]", cls._artist(task))
                if a.strip()]

    def _values(self, task):
        return [
            task.get("content") or "",
            (task.get("entity") or {}).get("name", ""),
            (task.get("project") or {}).get("name", ""),
            (task.get("step") or {}).get("name", ""),
            self._artist(task),
            names(task.get(config.TASK_LEAD_FIELD)),
        ]

    def _rebuild(self):
        in_project = filter_tasks(self._tasks,
                                  project_id=self.project_box.currentData())
        artist = self.artist_box.currentData() if self.scope != MINE \
            else None
        if artist:
            in_project = [t for t in in_project
                          if artist in self._artists(t)]
        self._refresh_chips(in_project)
        self._rows = self._sorted(filter_tasks(in_project, chip=self._chip))

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._rows))
        for r, t in enumerate(self._rows):
            for c, v in enumerate(self._values(t)):
                item = QTableWidgetItem(str(v))
                if c in (2, 3, self.COL_ARTIST, self.COL_REVIEWER):
                    item.setForeground(QColor(theme.TEXT_DIM))
                self.table.setItem(r, c, item)
            self._load_thumbnail(r, t)

            code = t.get("sg_status_list") or ""
            status = QTableWidgetItem(self._status_labels.get(code, code))
            status.setData(Qt.UserRole, code)       # the pill's colour
            self.table.setItem(r, self.COL_STATUS, status)

            start = QTableWidgetItem(start_label(t) or "—")
            start.setForeground(QColor(theme.TEXT_DIM if t.get("start_date")
                                       else theme.TEXT_FAINT))
            start.setToolTip(t.get("start_date") or "No start date")
            self.table.setItem(r, self.COL_START, start)

            due = QTableWidgetItem(due_label(t) or "—")
            due.setData(Qt.UserRole, t.get("due_date") or "")   # red / amber
            due.setToolTip(t.get("due_date") or "No due date")
            self.table.setItem(r, self.COL_DUE, due)
            self.table.setItem(r, self.COL_NOTES, self._notes_item(t))

            # The replaced button is only deleted on the next event loop pass;
            # hide it now so it never shows through for a frame.
            old = self.table.cellWidget(r, self.COL_LAUNCH)
            if old is not None:
                old.hide()
            self.table.setCellWidget(r, self.COL_LAUNCH, self._launch_button(t))
        self.table.setUpdatesEnabled(True)

        if self._loading and not self._tasks:
            return
        if self._rows:
            self.stack.setCurrentWidget(self.table)
        elif not self._tasks:
            self.stack.setCurrentWidget(self.caught_up)
        else:
            where = self.project_box.currentText() \
                if self.project_box.currentData() is not None else ""
            what = self._chip_label(self._chip) if self._chip != ALL else ""
            self.filtered_title.setText(
                "No tasks match" + (f" {what}" if what else "")
                + (f" for {artist}" if artist else "")
                + (f" in {where}" if where else ""))
            self.stack.setCurrentWidget(self.filtered_empty)

    def _load_thumbnail(self, row, task):
        """The shot's or asset's image as the Task cell's icon, fetched in the
        background and cached per URL by widgets.load_thumbnail."""
        url = task.get("entity.Shot.image") or task.get("entity.Asset.image")
        if not url:
            return

        def arrived(pixmap):
            item = self.table.item(row, 0)
            # A filter may have rebuilt the rows while this was in flight.
            if pixmap.isNull() or not item or row >= len(self._rows) or                     self._rows[row]["id"] != task["id"]:
                return
            item.setIcon(QIcon(pixmap.scaled(
                56, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)))

        load_thumbnail(url, arrived)

    def _notes_item(self, task):
        count, latest = self._notes.get(task["id"], (0, None))
        if not count:
            item = QTableWidgetItem("+ Add note")
            item.setForeground(QColor(theme.TEXT_FAINT))
            item.setToolTip("No notes yet — click to write one")
            return item
        dated = hasattr(latest, "strftime")
        item = QTableWidgetItem(
            f"✎ {count}  ·  {latest:%d %b}" if dated else f"✎ {count}")
        item.setToolTip(f"{count} note{'s' if count != 1 else ''}"
                        + (f", newest {latest:%d %b %Y %H:%M}" if dated else "")
                        + " — click to open")
        return item

    def _on_cell_clicked(self, row, col):
        if col == self.COL_NOTES and 0 <= row < len(self._rows):
            self.notes_requested.emit(self._rows[row])

    def _launch_button(self, task):
        """▶ the app last used on this task; the arrow lists every DCC."""
        btn = QToolButton()
        btn.setObjectName("launchBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedWidth(128)
        menu = QMenu(btn)
        menu.aboutToShow.connect(
            lambda m=menu, t=task: (m.clear(), self._add_launch_actions(m, t)))
        btn.setMenu(menu)

        last = self._last_launch.get(str(task["id"]))
        if last:
            package, version = last
            label = config.DCC_LABELS.get(package, package.title())
            btn.setText(f"▶  {label}")
            btn.setToolTip(f"Launch {label} {version} on this task")
            btn.setPopupMode(QToolButton.MenuButtonPopup)
            btn.setProperty("split", True)        # styled with an arrow area
            btn.clicked.connect(
                lambda _=False, t=task, p=package, v=version:
                self.package_launched.emit(t, p, v))
        else:
            btn.setText("▶  Open…  ▾")
            btn.setToolTip("Choose an app to open this task in")
            btn.setPopupMode(QToolButton.InstantPopup)
        return btn

    def _open_row(self, row, _col=0):
        if 0 <= row < len(self._rows):
            self.task_opened.emit(self._rows[row])
