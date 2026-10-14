import getpass

import datetime

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QMenu, QPushButton, QStackedWidget, QMessageBox, QSplitter, QInputDialog,
)

import applog, config, launcher, notes_service, paths, rv_player, status_mail
from sg_client import EVERYONE
from . import jobs
from .widgets import STYLE, UserChip
from .console import ConsolePanel
from .home_tasks import HomeTasks
from .project_page import ProjectPage
from .package_dialog import PackageDialog
from .publish_dialog import PublishDialog
from .review_page import ReviewPage
from .software_page import SoftwarePage
from .task_notes import TaskNotesDialog
from .task_search import TaskSearch
from .version_browser import VersionBrowser
from .version_compare import VersionCompare

log = applog.get()


def is_production(login):
    """Production opens on every artist's tasks rather than only their own.

    ponytail: a login list for now; ShotGrid's permission group decides this
    once managers and artists are told apart there.
    """
    return login in config.PRODUCTION_USERS


class MainWindow(QMainWindow):
    def __init__(self, sg, login=None, auth_result=None, production=None):
        super().__init__()
        self.sg = sg
        # login comes from auth.authenticate() in main(); the getpass fallback
        # keeps the window constructible in tests and from a shell.
        self.auth = auth_result
        self.login = login or (auth_result.login if auth_result else None) \
            or getpass.getuser()
        self.email = config.current_user_email(self.login)
        self.display_name = (auth_result.display_name if auth_result
                             else None) or self.login
        self.production = is_production(self.login) if production is None \
            else production
        self.sg.login = self.login      # signs the notes this session writes
        self.owner = None         # ShotGrid HumanUser, filled by _bootstrap
        self._projects = []       # full Project dicts, for the header search
        self._pending_task_id = None    # task to select once its project loads
        self.project = None
        self.task = None          # Task an app will be launched against
        self.pool = QThreadPool.globalInstance()
        self._jobs = set()        # in-flight workers, see _run()
        self._event_id = None     # newest ShotGrid event seen, for live sync
        self._syncing = False
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(max(config.SYNC_SECONDS, 1) * 1000)
        self._sync_timer.timeout.connect(self._poll_sync)

        self.setWindowTitle(config.APP_TITLE)
        self.resize(1080, 720)
        self.setMinimumSize(760, 480)
        self.setStyleSheet(STYLE)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header
        header = QWidget()
        header.setObjectName("header")
        header.setFixedHeight(54)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(4)

        self.back_btn = QPushButton("\u2039  Projects")
        self.back_btn.setObjectName("backBtn")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(self.show_projects)
        self.back_btn.hide()
        h.addWidget(self.back_btn)

        # Breadcrumb: app name, then the project once one is open.
        self.title = QLabel(config.APP_TITLE)
        self.title.setObjectName("headerTitle")
        h.addWidget(self.title)

        self.crumb = QLabel("\u203a")
        self.crumb.setObjectName("crumb")
        self.crumb.hide()
        h.addWidget(self.crumb)

        self.crumb_project = QLabel("")
        self.crumb_project.setObjectName("headerTitle")
        self.crumb_project.hide()
        h.addWidget(self.crumb_project)

        h.addStretch()

        # Find a task without remembering which show it is on.
        self.task_search = TaskSearch()
        self.task_search.tasks_needed.connect(self._load_all_tasks)
        self.task_search.task_chosen.connect(self.goto_task)
        h.addWidget(self.task_search)

        # Needs Attention sits in the header rather than in a tab: it is a
        # different question from "what are my tasks", and the artist has to
        # be able to see the count without going looking for it.
        self.review_btn = QPushButton("Needs Attention")
        self.review_btn.setObjectName("termBtn")
        self.review_btn.setCheckable(True)
        self.review_btn.setCursor(Qt.PointingHandCursor)
        self.review_btn.setToolTip(
            "Notes on your versions, replies to your notes, and versions a "
            "supervisor pushed back")
        self.review_btn.toggled.connect(self.show_review)
        h.addWidget(self.review_btn)

        # Only meaningful inside a project, so it comes and goes with one.
        self.deliver_btn = QPushButton("Client Delivery")
        self.deliver_btn.setObjectName("termBtn")
        self.deliver_btn.setCursor(Qt.PointingHandCursor)
        self.deliver_btn.setToolTip(
            "Package approved versions for the client using the project's "
            "delivery config")
        self.deliver_btn.clicked.connect(self.client_delivery)
        self.deliver_btn.hide()
        h.addWidget(self.deliver_btn)

        self.dashboard_btn = QPushButton("Dashboard ↗")
        self.dashboard_btn.setObjectName("termBtn")
        self.dashboard_btn.setCursor(Qt.PointingHandCursor)
        self.dashboard_btn.setToolTip(
            "Open the ingestion and packaging dashboard in the browser")
        self.dashboard_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(config.DASHBOARD_URL)))
        self.dashboard_btn.setVisible(
            self.production and bool(config.DASHBOARD_URL))
        h.addWidget(self.dashboard_btn)

        self.term_btn = QPushButton("Terminal")
        self.term_btn.setObjectName("termBtn")
        self.term_btn.setCheckable(True)
        self.term_btn.setCursor(Qt.PointingHandCursor)
        self.term_btn.setToolTip(
            "Show what Flow is running in the background (Ctrl+`)")
        self.term_btn.toggled.connect(self.toggle_console)
        h.addWidget(self.term_btn)

        h.addWidget(self._build_user_chip())
        root.addWidget(header)

        # pages, with the terminal as a collapsible bottom pane
        self.stack = QStackedWidget()
        # Home: the artist's open tasks from every project above the project
        # tiles, so the day's work is visible without choosing a show first.
        self.home_tasks = HomeTasks(production=self.production)
        self.project_page = ProjectPage()
        self.home = QWidget()
        home_lay = QVBoxLayout(self.home)
        home_lay.setContentsMargins(0, 0, 0, 0)
        home_lay.setSpacing(0)
        home_lay.addWidget(self.home_tasks, 3)
        home_lay.addWidget(self.project_page, 2)
        self.software_page = SoftwarePage()
        self.review_page = ReviewPage(sg)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.software_page)
        self.stack.addWidget(self.review_page)

        self.console = ConsolePanel()
        self.console.closed.connect(lambda: self.term_btn.setChecked(False))
        self.console.hide()

        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(self.stack)
        self.split.addWidget(self.console)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.split.setCollapsible(0, False)
        self.split.setHandleWidth(1)
        root.addWidget(self.split)

        QShortcut(QKeySequence("Ctrl+`"), self,
                  activated=self.term_btn.toggle)
        QShortcut(QKeySequence("Ctrl+K"), self,
                  activated=self.task_search.focus)

        self.project_page.project_selected.connect(self.open_project)
        self.home_tasks.task_opened.connect(self.goto_task)
        self.home_tasks.notes_requested.connect(self.open_task_notes)
        self.home_tasks.refresh_requested.connect(self._load_open_tasks)
        self.home_tasks.scope_changed.connect(self._load_open_tasks)
        self.home_tasks.package_launched.connect(self.launch_package)
        self.home_tasks.folder_requested.connect(self.open_folder)
        self.home_tasks.status_change_requested.connect(self.set_task_status)
        self.home_tasks.publish_requested.connect(self.publish_version)
        self.home_tasks.versions_requested.connect(self.view_versions)
        self.home_tasks.latest_version_requested.connect(
            self.open_latest_version)
        self.software_page.software_launched.connect(self.launch_software)
        self.software_page.task_selected.connect(self._on_task_selected)
        self.software_page.package_launched.connect(self.launch_package)
        self.software_page.folder_requested.connect(self.open_folder)
        self.software_page.status_change_requested.connect(self.set_task_status)
        self.software_page.publish_requested.connect(self.publish_version)
        self.software_page.versions_requested.connect(self.view_versions)
        self.software_page.latest_version_requested.connect(
            self.open_latest_version)
        self.review_page.item_opened.connect(self.open_review_item)
        self.review_page.compare_requested.connect(self.compare_review_item)
        self.review_page.count_changed.connect(self._on_review_count)

        self.statusBar().showMessage("Connecting to ShotGrid...")
        self._run(self._bootstrap, self._on_bootstrap)

    # -- signed-in user ----------------------------------------------------

    AUTH_METHOD_LABELS = {
        "sso": "Workstation login (Active Directory)",
        "bind": "Password sign-in (Active Directory)",
        "dev": "Developer override (SGDESK_DEV_USER)",
        "disabled": "Authentication disabled in auth_config.yml",
    }

    def _build_user_chip(self):
        self.user_chip = UserChip(self.display_name, avatar_text=self.email)
        self.user_chip.setToolTip("Who Flow is signed in as")
        self.user_chip.clicked.connect(self._show_user_menu)
        # Kept for the tests and for anything that reads the header text.
        self.user_lbl = self.user_chip.name_lbl
        return self.user_chip

    def _user_details(self):
        """(label, value) rows for the profile menu, in reading order."""
        rows = [("Name", self.display_name),
                ("Login", self.login),
                ("Email", self.email)]
        if self.production:
            rows.append(("View", "Production — opens on every artist's tasks"))
        if self.auth is not None:
            rows.append(("Signed in with",
                         self.AUTH_METHOD_LABELS.get(self.auth.method,
                                                     self.auth.method)))
            if getattr(self.auth, "domain", ""):
                rows.append(("Domain", self.auth.domain))
        if self.owner:
            rows.append(("ShotGrid",
                         self.owner.get("name") or self.owner.get("login", "")))
        else:
            rows.append(("ShotGrid", f"no {config.TASK_OWNER_ENTITY} matched "
                                     f"{self.email}"))
        return rows

    def _show_user_menu(self):
        menu = QMenu(self)
        for label, value in self._user_details():
            action = menu.addAction(f"{label}:  {value}")
            action.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Copy email address",
                       lambda: QApplication.clipboard().setText(self.email))
        menu.addAction("Copy login", lambda: QApplication.clipboard()
                       .setText(self.login))
        menu.exec(self.user_chip.mapToGlobal(
            self.user_chip.rect().bottomLeft()))

    # -- review inbox ------------------------------------------------------

    def show_review(self, shown):
        if shown:
            self._previous_page = self.stack.currentWidget()
            self.stack.setCurrentWidget(self.review_page)
            self.review_page.refresh()
        elif getattr(self, "_previous_page", None) is not None:
            self.stack.setCurrentWidget(self._previous_page)

    def _on_review_count(self, unread):
        self.review_btn.setText(
            f"Needs Attention ({unread})" if unread else "Needs Attention")
        # The same review data drives the dot on the task rows, so the two can
        # never disagree.
        self.software_page.set_attention(self.review_page.attention_by_task())

    def open_review_item(self, item):
        """Straight to the version the item is about, not to a search box."""
        version_id = (item.version or {}).get("id")
        if not version_id:
            QMessageBox.information(
                self, "Needs Attention",
                "This item is not linked to a version.")
            return
        task = item.task or self.task
        project = item.project or self.project
        if not task or not task.get("id"):
            self._open_version_in_browser(project, item)
            return
        self.review_btn.setChecked(False)
        browser = VersionBrowser(self.sg, project, self._full_task(task), self)
        browser.select_version(version_id)
        browser.exec()

    def _open_version_in_browser(self, project, item):
        QMessageBox.information(
            self, "Needs Attention",
            f"{item.headline()} on {item.version.get('code') or 'a version'}"
            f"\n\nThis note is not linked to a task, so Flow cannot open "
            f"the task's version list for it.")

    def _full_task(self, task):
        """The Task dict the browser needs, from the tasks already loaded."""
        for loaded in self.software_page.tasks._tasks:
            if loaded["id"] == task.get("id"):
                return loaded
        return dict(task, id=task["id"],
                    content=task.get("name") or task.get("content", ""))

    def compare_review_item(self, item):
        version_id = (item.version or {}).get("id")
        if not version_id:
            return
        self._run(lambda: self.sg.compare_pair(version_id),
                  lambda pair: self._show_compare(*pair),
                  on_error=lambda m: QMessageBox.warning(
                      self, "Compare", m))

    def _show_compare(self, newer, older):
        if not older:
            QMessageBox.information(
                self, "Compare",
                f"{newer.get('code') or 'This version'} is the first version "
                f"on its task — there is nothing before it to compare with.")
            return
        # Both playable: RV's own wipe, at full resolution and in the project's
        # colour space. The dialog is the fallback for media RV cannot reach.
        if rv_player.playable(newer) and rv_player.playable(older):
            try:
                rv_player.open_versions([newer, older], mode="wipe",
                                        project=self.project)
                return
            except Exception as e:
                log.error("could not open RV: %s", e)
        VersionCompare(self.sg, self.project, newer, older, parent=self).exec()

    def open_latest_version(self, task, version):
        """Open the task's newest version, selected in the browser."""
        self.task = task
        self.software_page.set_task(task)
        browser = VersionBrowser(self.sg, self._task_project(task), task, self)
        browser.select_version(version["id"])
        browser.exec()

    # -- terminal ----------------------------------------------------------

    def toggle_console(self, shown):
        self.console.setVisible(shown)
        # Hiding alone is not enough: the splitter keeps the pane's share and
        # leaves a dead strip at the bottom, so the sizes are set by hand.
        total = sum(self.split.sizes()) or self.split.height() or self.height()
        if shown:
            self.split.setSizes([int(total * 0.62), int(total * 0.38)])
        else:
            self.split.setSizes([total, 0])

    def show_console(self):
        if not self.term_btn.isChecked():
            self.term_btn.setChecked(True)

    # -- async helper ------------------------------------------------------

    def _run(self, fn, on_result, *args, on_error=None):
        """Run fn on the thread pool and hand the result back on the UI thread."""
        return jobs.run(
            self._jobs, fn, on_result, *args, pool=self.pool,
            on_error=on_error or
            (lambda msg: QMessageBox.critical(self, "ShotGrid", msg)))

    # -- data --------------------------------------------------------------

    def _bootstrap(self):
        owner = self.sg.resolve_owner(self.email)
        projects = self.sg.active_projects()
        statuses = self.sg.task_statuses()
        return owner, projects, statuses

    def _on_bootstrap(self, result):
        owner, projects, statuses = result
        self.owner = owner
        self._projects = projects
        # Signs and credits the notes a lead sends from RV.
        rv_player.reviewer = {"login": self.login, "user": owner}
        self.software_page.set_statuses(statuses)
        self.home_tasks.set_statuses(statuses)
        if owner and owner.get("name"):
            self.user_chip.set_name(owner["name"])
        if not owner and config.TASK_OWNER_IS_ENTITY:
            self.statusBar().showMessage(
                f"No {config.TASK_OWNER_ENTITY} found with "
                f"{config.TASK_OWNER_MATCH_FIELD} '{self.email}' "
                f"— My Tasks will be empty")
        elif not owner:
            # String mode still works without the entity: it falls back to the
            # email address, which is probably what the tasks store anyway.
            self.statusBar().showMessage(
                f"Connected — {len(projects)} projects "
                f"(no {config.TASK_OWNER_ENTITY} for '{self.email}'; "
                f"matching tasks on the address itself)")
        else:
            self.statusBar().showMessage(f"Connected — {len(projects)} projects")
        self.project_page.set_projects(projects)
        self.home_tasks.set_projects(projects)
        self._load_open_tasks()
        if config.SYNC_SECONDS > 0:
            self._poll_sync()                 # takes the starting event id
            self._sync_timer.start()

    # -- live sync -----------------------------------------------------------

    def _poll_sync(self):
        # One query at a time, and none while nobody can see the window.
        if self._syncing or self.isMinimized():
            return
        self._syncing = True

        def failed(message):
            self._syncing = False
            log.warning("live sync skipped: %s", message)

        self._run(self.sg.task_events_since, self._on_sync, self._event_id,
                  on_error=failed)

    def _on_sync(self, result):
        self._syncing = False
        starting = self._event_id is None
        self._event_id, events = result
        if starting or not self._sync_relevant(events):
            return
        self._load_open_tasks()
        self.statusBar().showMessage(
            "ShotGrid changes synced "
            + datetime.datetime.now().strftime("%H:%M"), 10000)

    def _sync_relevant(self, events):
        """Whether any event could change the home list as it stands."""
        if self.home_tasks.scope == EVERYONE:
            return bool(events)
        shown = {t["id"] for t in self.home_tasks._tasks}
        me = (self.sg.owner_value or "").lower()
        for e in events:
            if (e.get("entity") or {}).get("id") in shown:
                return True
            # A task newly given to this user, or to them to review. ShotGrid
            # logs the fields set on a new task as changes too.
            if me and e.get("attribute_name") in (config.TASK_OWNER_FIELD,
                                                  config.TASK_LEAD_FIELD)                     and me in str((e.get("meta") or {}).get("new_value")
                                  or "").lower():
                return True
        return False

    def _load_open_tasks(self):
        """The home page's task list: every unfinished task, all projects."""
        self.home_tasks.set_loading()
        scope = self.home_tasks.scope
        self._run(lambda: self.sg.open_tasks(scope=scope),
                  # A switch flipped mid-load: only the newest scope lands.
                  lambda tasks: scope == self.home_tasks.scope
                  and self._on_open_tasks(tasks),
                  on_error=lambda m: (
                      log.warning("could not load open tasks: %s", m),
                      self.home_tasks.set_tasks([], failed=True)))

    def _on_open_tasks(self, tasks):
        self.home_tasks.set_tasks(tasks)
        self._load_task_notes()
        # ponytail: Everyone skips Latest Version -- every version on every
        # show is too big a query; page it per project if they ask for it.
        if tasks and self.home_tasks.scope != EVERYONE:
            # For the menu's Latest Version entry: one query for the lot.
            self._run(self.sg.latest_versions_for_tasks,
                      self.home_tasks.set_latest_versions,
                      [t["id"] for t in tasks],
                      on_error=lambda m: log.warning(
                          "could not read latest versions: %s", m))

    def _load_task_notes(self):
        """The Notes column: count and newest date, one pass for the list."""
        ids = [t["id"] for t in self.home_tasks._tasks]
        if not ids:
            return
        self._run(notes_service.NotesService(self.sg).task_summary,
                  self.home_tasks.set_notes, ids,
                  on_error=lambda m: log.warning(
                      "could not read task notes: %s", m))

    def open_task_notes(self, task):
        dialog = TaskNotesDialog(self.sg, self._task_project(task), task, self)
        dialog.posted.connect(self._load_task_notes)
        dialog.exec()

    # -- searching every project's tasks -------------------------------------

    def _load_all_tasks(self):
        """One query for the artist's whole workload, for the header search."""
        self._run(self.sg.all_my_tasks, self.task_search.set_tasks,
                  on_error=lambda m: log.warning(
                      "could not load the task search list: %s", m))

    def goto_task(self, task):
        """Open the task's project and land on the task itself."""
        project = self._project_for(task)
        if not project:
            QMessageBox.information(
                self, "Search",
                f"'{task.get('content', 'That task')}' is not on a project "
                f"Flow can open.")
            return
        if not self.project or self.project["id"] != project["id"]:
            self._pending_task_id = task["id"]
            self.open_project(project)
            return
        self._select_task(task["id"])

    def _project_for(self, task):
        """The full Project dict — the task's own carries only id and name."""
        link = task.get("project") or {}
        for project in self._projects:
            if project["id"] == link.get("id"):
                return project
        return link if link.get("id") else None

    def _task_project(self, task):
        """The project a task action runs in: the task's own, not the page's.

        The home page's menu acts on tasks from every project, so the
        handlers cannot assume the open project is the right one.
        """
        return self._project_for(task) or self.project

    def _select_task(self, task_id):
        if self.software_page.select_task(task_id):
            return True
        log.info("task %s is not in this project's task list", task_id)
        return False

    # -- navigation ---------------------------------------------------------

    def show_projects(self):
        self.project = None
        self.back_btn.hide()
        self.deliver_btn.hide()
        self.crumb.hide()
        self.crumb_project.hide()
        self.stack.setCurrentWidget(self.home)
        # Statuses may have changed inside the project; one query catches up.
        self._load_open_tasks()

    def open_project(self, project):
        self.project = project
        self.task = None
        self.software_page.set_task(None)
        self.software_page.set_project(project)
        self.crumb_project.setText(project["name"])
        self.crumb.show()
        self.crumb_project.show()
        self.back_btn.show()
        self.deliver_btn.show()
        self.stack.setCurrentWidget(self.software_page)
        self.software_page.set_software([])
        self.software_page.set_loading()
        self.statusBar().showMessage("Loading apps and tasks...")

        self._run(self.sg.software_for_project, self._on_software, project)
        scope = self.home_tasks.scope
        self._run(lambda p: self.sg.my_tasks(p, scope=scope),
                  self._on_tasks, project)

    def _on_software(self, softwares):
        self.software_page.set_software(softwares)
        self.statusBar().showMessage(f"{len(softwares)} apps available")

    def _on_tasks(self, tasks):
        self.software_page.set_tasks(tasks)
        if self._pending_task_id is not None:
            # Arrived here from the header search: the tasks only exist now.
            self._select_task(self._pending_task_id)
            self._pending_task_id = None
        if not tasks:
            return
        # One query for the whole page. A query per task is the N+1 that makes
        # a task list crawl on a show with real history.
        task_ids = [t["id"] for t in tasks]
        self._run(self.sg.latest_versions_for_tasks,
                  self.software_page.set_latest_versions, task_ids,
                  on_error=lambda m: log.warning(
                      "could not read latest versions: %s", m))
        # The review pass fills the dots on the same rows; it is separate
        # because it is worth having even when it fails.
        self.review_page.set_project(self.project)
        self.review_page.refresh()

    # -- launch --------------------------------------------------------------

    def _on_task_selected(self, task):
        self.task = task

    def launch_package(self, task, package, version):
        """Right-click launch: a DCC from the rez tree, opened on this task."""
        self.task = task
        self.software_page.set_task(task)
        try:
            pid, log_path = launcher.launch_package(
                self._task_project(task), package, version, task,
                self.login, self.email)
        except Exception as e:
            self._launch_failed(f"{package}-{version}", e)
            return
        self.console.tail(log_path)
        self.home_tasks.remember_launch(task, package, version)
        self.statusBar().showMessage(
            f"Launched {package}-{version} (pid {pid}) "
            f"on task '{task.get('content', '')}' — see Terminal for output")

    def launch_software(self, software):
        try:
            pid, log_path = launcher.launch(
                self.project, software, self.login, self.email, self.task)
        except Exception as e:
            self._launch_failed(software.get("code", "app"), e)
            return
        self.console.tail(log_path)
        where = (f"on task '{self.task['content']}'" if self.task
                 else "with no task context")
        self.statusBar().showMessage(
            f"Launched {software['code']} (pid {pid}) "
            f"in {self.project['name']} {where} — see Terminal for output")

    def publish_version(self, task):
        """Standalone publish: upload media as a Version against this task."""
        self.task = task
        self.software_page.set_task(task)
        log.info("standalone publish for task %s (%s)",
                 task["id"], task.get("content", ""))

        dialog = PublishDialog(self.sg, self._task_project(task), task,
                               self.email, self)
        dialog.exec()
        result = dialog.published
        if result:
            self.statusBar().showMessage(
                f"Published Version {result.code} to "
                f"'{task.get('content', '')}' as {self.sg.api_identity}")

    def client_delivery(self):
        if self.project:
            PackageDialog(self.sg, self.project, self).exec()

    def view_versions(self, task):
        """Browse what has already been published on this task's entity."""
        self.task = task
        self.software_page.set_task(task)
        if not task.get("entity"):
            QMessageBox.information(
                self, "Versions",
                "This task is not linked to a shot or asset, so it has no "
                "versions to browse.")
            return
        log.info("version browser for %s %s (task %s)",
                 task["entity"].get("type"), task["entity"].get("name"),
                 task["id"])
        VersionBrowser(self.sg, self._task_project(task), task, self).exec()

    def set_task_status(self, task, code):
        """Write a status change back to ShotGrid, off the UI thread."""
        task_id = task["id"]
        old = task.get("sg_status_list") or ""
        mails = status_mail.wants_mail(code)
        note = self._ask_status_note(task, code) if mails else ""
        if note is None:
            return                      # cancelled: the status stays as it was
        self.statusBar().showMessage(
            f"Setting '{task.get('content', '')}' to {code}…")
        # Show it straight away; put it back if the write fails.
        self.software_page.update_task_status(task_id, code)
        self.home_tasks.update_task(task_id, code)

        def write():
            # The note goes first: the SG daemon mails the leads on the
            # status change and attaches the task's newest note.
            if note:
                self._post_status_note(task, note, code)
            self.sg.set_task_status(task_id, code)
            mailed = self._mail_status_change(task, note, old, code) \
                if mails else ""
            return task_id, code, mailed

        def done(result):
            _, new_code, mailed = result
            self.statusBar().showMessage(
                f"'{task.get('content', '')}' is now {new_code}"
                + (f" - {mailed}" if mailed else ""))

        def failed(msg):
            log.error("could not set status on task %s: %s", task_id, msg)
            self.software_page.update_task_status(task_id, old)
            self._load_open_tasks()      # may have dropped it as finished
            self.show_console()
            QMessageBox.warning(
                self, "Status not changed",
                f"ShotGrid rejected the change:\n\n{msg}\n\n"
                f"The task is still {old or 'unset'}.")

        self._run(write, done, on_error=failed)

    def _ask_status_note(self, task, code):
        """The artist's note for whoever gets the mail; None if they cancel."""
        who = "the leads" if code == config.REVIEW_MAIL_STATUS \
            else "production"
        text, ok = QInputDialog.getMultiLineText(
            self, f"Set to {code}",
            f"Note for {who} on '{task.get('content', '')}':")
        return text.strip() if ok else None

    def _post_status_note(self, task, note, code):
        """The artist's note on the task. A failure is logged, never stops
        the status change."""
        try:
            self.sg.create_note(task["project"], None, note,
                                f"Set to {code}", task)
        except Exception as e:
            log.warning("status note on task %s failed: %s", task["id"], e)

    def _mail_status_change(self, task, note, old, code):
        """Mail whoever acts next. On the worker, after the status is saved:
        a failure here is reported, never undoes it."""
        if code == config.REVIEW_MAIL_STATUS:
            return "the leads are mailed by ShotGrid"
        try:
            to = status_mail.send(
                self.sg.sg, task, notes_service.signer_name(self.login),
                self.email, note, old, code)
        except Exception as e:
            log.warning("%s mail for task %s failed: %s", code, task["id"], e)
            return f"mail failed: {e}"
        return f"mailed {', '.join(to)}" if to else "nobody to mail"

    def open_folder(self, path):
        try:
            paths.open_folder(path)
            self.statusBar().showMessage(f"Opened {path}")
        except Exception as e:
            log.error("could not open %s: %s", path, e)
            QMessageBox.warning(self, "Open folder", str(e))

    def _launch_failed(self, what, error):
        """Open the Terminal on failure: the log says more than the dialog."""
        log.error("launch of %s failed: %s", what, error)
        self.show_console()
        QMessageBox.critical(
            self, "Launch failed",
            f"{error}\n\nThe Terminal panel shows the command that was tried.")
