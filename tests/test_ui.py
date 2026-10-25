"""The parts of the UI that carry context: menus, the dialog, the browser.

Headless (QT_QPA_PLATFORM=offscreen, set by tests/run.py). Nothing here opens a
modal: `QMenu.exec` cannot be monkeypatched on a PySide class, and triggering
the publish action would open a real dialog and hang the run -- so the menu
building and the dialogs are driven directly instead.
"""

import os
import tempfile

from PySide6.QtCore import QThreadPool, QMimeData, QUrl, QPointF, Qt
from PySide6.QtGui import QPixmap, QColor, QDropEvent, QDragEnterEvent
from PySide6.QtWidgets import QApplication, QFrame, QMenu
from PySide6.QtWidgets import QLabel as QLabelType

import config
import fakes
import publish_service

app = None
TMP = tempfile.mkdtemp(prefix="flow-ui-")
PNG = os.path.join(TMP, "SH010_comp_v004.png")
NK = os.path.join(TMP, "SH010_comp_v004.nk")


def setup_module():
    global app
    app = QApplication.instance() or QApplication([])
    pm = QPixmap(400, 225)
    pm.fill(QColor("#3d9dff"))
    pm.save(PNG)
    with open(NK, "w") as f:
        f.write("# nuke script\n")


def settle(ms=4000):
    app.processEvents()
    QThreadPool.globalInstance().waitForDone(ms)
    for _ in range(8):
        app.processEvents()


# -- the task context menu -------------------------------------------------

def test_right_click_offers_publish_with_the_task_attached():
    from ui.software_page import TasksTable

    table = TasksTable()
    menu = QMenu()
    sub = table._add_publish_actions(menu, fakes.TASK)

    got = []
    table.publish_requested.connect(got.append)
    [a for a in sub.actions() if "Standalone" in a.text()][0].trigger()
    assert got and got[0]["id"] == fakes.TASK["id"], \
        "the publish must carry the task that was right-clicked"


def test_right_click_offers_view_versions():
    from ui.software_page import TasksTable

    table = TasksTable()
    menu = QMenu()
    sub = table._add_version_actions(menu, fakes.TASK)

    got = []
    table.versions_requested.connect(got.append)
    actions = [a for a in sub.actions() if "View Versions" in a.text()]
    assert actions, "Versions ▸ View Versions should be in the menu"
    actions[0].trigger()
    assert got and got[0]["id"] == fakes.TASK["id"]


def test_view_versions_is_disabled_without_an_entity():
    from ui.software_page import TasksTable

    table = TasksTable()
    menu = QMenu()          # held: a collected QMenu takes its submenu with it
    sub = table._add_version_actions(menu, fakes.TASK_NO_ENTITY)
    assert not sub.actions()[0].isEnabled()


# -- the publish dialog ----------------------------------------------------

def _dialog(sg=None):
    from ui.publish_dialog import PublishDialog
    client = fakes.client(sg)
    dialog = PublishDialog(client, fakes.PROJECT, fakes.TASK,
                           fakes.ARTIST["email"])
    # Shown even offscreen: a child widget of a hidden window reports
    # isVisible() False, and drops are only delivered to a shown widget.
    dialog.show()
    settle(500)
    return dialog


def test_dialog_shows_the_task_context_and_suggests_a_name():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v003")
    dialog = _dialog(sg)
    assert dialog.name_edit.text() == "SH010_Comp_v004"
    assert dialog.task["id"] == fakes.TASK["id"]
    assert not dialog.publish_btn.isEnabled(), "nothing chosen yet"


def test_drop_routes_media_and_scene_files():
    dialog = _dialog()
    _drop(dialog, [PNG, NK])
    # Compared per path element: a dropped URL comes back with forward slashes
    # even on Windows, which says nothing about the routing.
    assert _same(dialog.file_edit.text(), PNG)
    assert _same(dialog.work_edit.text(), NK)
    assert dialog.publish_btn.isEnabled()


def test_dropping_an_unsupported_file_blocks_the_publish():
    dialog = _dialog()
    txt = os.path.join(TMP, "notes.txt")
    open(txt, "w").write("hello")
    _drop(dialog, [txt])
    assert not dialog.publish_btn.isEnabled()
    assert "not a publishable media format" in dialog.file_info.text()


def test_duplicate_name_warns_and_blocks_before_any_upload():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v004")
    dialog = _dialog(sg)
    dialog.file_edit.setText(PNG)
    dialog.name_edit.setText("SH010_Comp_v004")
    dialog._check_name()
    settle(500)
    assert dialog.name_warning.isVisible()
    assert "already exists" in dialog.name_warning.text()
    assert not dialog.publish_btn.isEnabled()
    assert not sg.uploads


def test_media_is_inspected_off_the_ui_thread():
    dialog = _dialog()
    _choose(dialog, PNG)
    assert dialog.media_info is not None
    assert "400 × 225" in dialog.file_info.text()


def test_preflight_reports_before_anything_is_created():
    sg = fakes.FakeShotgun()
    dialog = _dialog(sg)
    _choose(dialog, PNG)
    assert dialog.report is not None
    assert not any(c[0] == "create" for c in sg.calls), \
        "the preflight must not create anything"
    text = _labels(dialog.preflight_panel)
    assert "Project: UAT6" in text
    assert "Version name available" in text


def test_a_warning_must_be_accepted_before_publish_is_offered():
    """The fixtures live in the OS temp directory, which is exactly the case
    the path policy exists for."""
    dialog = _dialog()
    dialog.file_edit.setText(PNG)
    dialog._run_preflight()
    settle()
    assert dialog.report.warnings
    assert dialog.accept_warnings.isVisible()
    assert not dialog.publish_btn.isEnabled(), \
        "a warning must not be publishable until it is acknowledged"

    dialog.accept_warnings.setChecked(True)
    assert dialog.publish_btn.isEnabled()


def test_an_error_cannot_be_ticked_past():
    dialog = _dialog()
    txt = os.path.join(TMP, "notes.txt")
    open(txt, "w").write("hello")
    dialog.file_edit.setText(txt)
    dialog._run_preflight()
    settle()
    assert not dialog.report.passed
    assert not dialog.accept_warnings.isVisible(), \
        "there is no continuing past an error"
    assert not dialog.publish_btn.isEnabled()


def test_media_from_another_project_is_refused_in_the_dialog():
    dialog = _dialog()
    dialog.file_edit.setText("/jobs/SHOW002/renders/v001.mov")
    dialog._run_preflight()
    settle()
    assert not dialog.publish_btn.isEnabled()


def test_publish_shows_a_result_with_the_api_identity():
    sg = fakes.FakeShotgun()
    dialog = _dialog(sg)
    _choose(dialog, PNG)
    dialog._publish()
    settle()

    assert dialog.published is not None
    assert sg.uploads, "the media should have been uploaded"
    assert dialog.pages.currentWidget() is dialog.result_page
    labels = _labels(dialog.result_page)
    assert config.SG_SCRIPT_NAME in labels
    assert fakes.ARTIST["email"] in labels
    assert "Publish Successful" in dialog.result_page.heading.text()


def test_failed_publish_keeps_the_dialog_open_and_says_why():
    sg = fakes.FakeShotgun()
    sg.fail_create = RuntimeError("CRUD ERROR: Create on Version not permitted")
    dialog = _dialog(sg)
    dialog.file_edit.setText(PNG)
    settle()
    # The message box would block, so the handler is driven directly with the
    # error the service produces.
    dialog._on_failed(publish_service.friendly(sg.fail_create))
    assert dialog.published is None
    assert "permission" in dialog.status.text().lower()


def test_cancel_during_a_publish_asks_the_job_to_stop():
    dialog = _dialog()
    dialog.file_edit.setText(PNG)
    settle()

    class Job:
        cancelled = False

        def cancel(self):
            Job.cancelled = True

    dialog._job = Job()
    dialog._on_cancel()
    assert Job.cancelled
    assert "Cancelling" in dialog.status.text()


# -- the version browser ---------------------------------------------------

def _browser(sg):
    from ui.version_browser import VersionBrowser
    browser = VersionBrowser(fakes.client(sg), fakes.PROJECT, fakes.TASK)
    browser.show()
    settle()
    return browser


def test_comparing_two_versions_opens_flows_player():
    from ui import version_browser
    sg = fakes.FakeShotgun()
    for i in (1, 2):
        sg.add_version(f"SH010_Comp_v00{i}")
    browser = _browser(sg)
    a, b = browser._versions[0], browser._versions[1]
    opened = []

    class Recorder:
        def __init__(self, sg, project, version_a, version_b, **kwargs):
            opened.append((version_a, version_b))

        def exec(self):
            return 0

    real = version_browser.VersionCompare
    version_browser.VersionCompare = Recorder
    try:
        browser._compare(a, b)
        browser._play_in_flow(a)
    finally:
        version_browser.VersionCompare = real
    assert opened == [(a, b), (a, None)]


def test_the_player_hands_its_wipe_to_rv():
    import rv_player
    from ui.version_compare import WIPE
    a = _image_version("rv_a")
    b = _image_version("rv_b", colour="#f87171")
    sent = {}
    playable, open_versions = rv_player.playable, rv_player.open_versions
    rv_player.playable = lambda v: True
    rv_player.open_versions = (lambda versions, mode="", project=None:
                               sent.update(versions=versions, mode=mode)
                               or (1, "log"))
    try:
        dialog = _compare(a, b)
        dialog.set_mode(WIPE)
        dialog.rv_btn.click()
    finally:
        rv_player.playable, rv_player.open_versions = playable, open_versions
    assert sent["mode"] == "wipe"
    assert sent["versions"] == [a, b]


def test_browser_lists_versions_newest_first():
    sg = fakes.FakeShotgun()
    for i in range(1, 4):
        sg.add_version(f"SH010_Comp_v00{i}")
    browser = _browser(sg)
    assert browser.table.rowCount() == 3
    assert browser.table.item(0, 0).text() == "SH010_Comp_v003"
    assert "3 versions" in browser.count_lbl.text()


def test_browser_shows_department_user_status_and_date():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v001",
                   **{"sg_task.Task.step": {"type": "Step", "id": 9,
                                            "name": "Comp"}})
    browser = _browser(sg)
    row = [browser.table.item(0, c).text() for c in range(5)]
    assert row[0] == "SH010_Comp_v001"
    assert row[1] == "Comp"
    assert row[2] == "Jitesh"
    assert row[3] == "rev"
    assert row[4]


def test_browser_filters_are_sent_to_shotgrid():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v001", sg_status_list="apr")
    sg.add_version("SH010_Comp_v002", sg_status_list="wip")
    browser = _browser(sg)

    browser.status.setCurrentIndex(
        [browser.status.itemData(i)
         for i in range(browser.status.count())].index("apr"))
    settle()
    assert browser.table.rowCount() == 1
    assert browser.table.item(0, 0).text() == "SH010_Comp_v001"

    find = [c for c in sg.calls if c[0] == "find" and c[1] == "Version"][-1]
    assert ["sg_status_list", "is", "apr"] in find[2], \
        "the status filter must be a query, not a pass over the rows"


def test_browser_search_is_debounced_then_queried():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v001", description="client notes")
    sg.add_version("SH010_Roto_v001", description="paint pass")
    browser = _browser(sg)
    before = len([c for c in sg.calls if c[0] == "find"])

    browser.search.setText("Roto")
    app.processEvents()
    assert len([c for c in sg.calls if c[0] == "find"]) == before, \
        "typing should not query on every keystroke"

    browser._search_timer.timeout.emit()      # the debounce firing
    settle()
    assert browser.table.rowCount() == 1
    assert browser.table.item(0, 0).text() == "SH010_Roto_v001"


def test_browser_scope_can_narrow_to_the_task():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v001")
    sg.add_version("SH010_Light_v001",
                   sg_task={"type": "Task", "id": 999})
    browser = _browser(sg)
    assert browser.table.rowCount() == 2

    browser.scope.setCurrentIndex(1)          # this task only
    settle()
    assert browser.table.rowCount() == 1
    assert browser.table.item(0, 0).text() == "SH010_Comp_v001"


def test_browser_pages_rather_than_loading_everything():
    sg = fakes.FakeShotgun()
    for i in range(1, 8):
        sg.add_version(f"SH010_Comp_v00{i}")
    old = config.VERSION_PAGE_SIZE
    config.VERSION_PAGE_SIZE = 3
    try:
        browser = _browser(sg)
        assert browser.table.rowCount() == 3
        assert browser.more_btn.isVisible()
        browser._load_more()
        settle()
        assert browser.table.rowCount() == 6
    finally:
        config.VERSION_PAGE_SIZE = old


def test_browser_details_only_show_fields_the_site_filled_in():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v001", description="Final comp with notes",
                   frame_range="1001-1120")
    browser = _browser(sg)
    browser.table.selectRow(0)
    settle()
    text = _labels(browser)
    assert "Final comp with notes" in text
    assert "1001-1120" in text
    assert "Media path" not in text, \
        "a field this version does not have should not be shown"


def test_browser_survives_a_failed_query():
    sg = fakes.FakeShotgun()
    sg.fail_find = RuntimeError("Permission denied on Version")
    browser = _browser(sg)
    assert browser.stack.currentWidget() is browser.empty
    assert "Permission denied" in browser.count_lbl.text()
    assert not browser.more_btn.isVisible()


def test_browser_empty_state_when_nothing_matches():
    browser = _browser(fakes.FakeShotgun())
    assert browser.stack.currentWidget() is browser.empty
    assert browser.count_lbl.text() in ("", "0 versions")


# -- notes in the browser --------------------------------------------------

def _browser_with_notes():
    sg = fakes.FakeShotgun()
    version = sg.add_version("SH010_Comp_v004")
    note = sg.add_note(version["id"], "Reduce brightness.",
                       user=fakes.CLIENT)
    sg.add_reply(note["id"], "Can Comp investigate?", user=fakes.PRODUCER)
    sg.add_reply(note["id"], "Updated in v005.", user=fakes.ARTIST)
    browser = _browser(sg)
    browser.tabs.setCurrentIndex(1)          # Notes
    browser.table.selectRow(0)
    settle()
    return sg, browser


def test_notes_show_for_the_selected_version_with_their_authors():
    sg, browser = _browser_with_notes()
    text = _labels(browser.notes)
    assert "Reduce brightness." in text
    assert "Sam" in text and "CLIENT" in text
    assert "Can Comp investigate?" in text
    assert "Updated in v005." in text


def test_replies_are_drawn_under_their_note():
    from ui.notes_panel import MessageCard

    sg, browser = _browser_with_notes()
    cards = browser.notes.findChildren(MessageCard)
    kinds = [c.message.kind for c in cards]
    assert kinds == ["note", "reply", "reply"]
    assert all(c.message.depth == 1 for c in cards if c.message.kind == "reply")


def test_edit_and_delete_only_appear_on_your_own_messages():
    from PySide6.QtWidgets import QPushButton
    from ui.notes_panel import MessageCard

    sg, browser = _browser_with_notes()
    cards = browser.notes.findChildren(MessageCard)
    buttons = {c.message.author_name:
               [b.text() for b in c.findChildren(QPushButton)]
               for c in cards}
    assert "Delete" not in buttons["Sam"], "the client's note is not ours"
    assert "Delete" in buttons["Jitesh"], "our own reply is"


def test_posting_a_note_writes_to_shotgrid_and_reloads_notes_only():
    sg, browser = _browser_with_notes()
    version_queries = len([c for c in sg.calls
                           if c[0] == "find" and c[1] == "Version"])

    browser.notes.compose.setPlainText("Grade updated.")
    browser.notes._post()
    settle()

    created = [c for c in sg.calls if c[0] == "create" and c[1] == "Note"]
    assert created and created[0][2]["content"] == "Grade updated."
    assert "Grade updated." in _labels(browser.notes)
    assert len([c for c in sg.calls
                if c[0] == "find" and c[1] == "Version"]) == version_queries, \
        "refreshing notes must not re-query the version list"


def test_replying_from_the_card_creates_a_reply():
    sg, browser = _browser_with_notes()
    thread = browser.notes.threads[0]
    browser.notes._start_reply(thread)
    browser.notes.compose.setPlainText("On it.")
    browser.notes._post()
    settle()
    assert sg.replies[-1]["content"] == "On it."
    assert sg.replies[-1]["entity"]["id"] == thread.id


def test_answering_a_reply_stays_in_its_thread():
    sg, browser = _browser_with_notes()
    thread = browser.notes.threads[0]
    browser.notes._start_reply(thread.replies[0])
    browser.notes.compose.setPlainText("Agreed.")
    browser.notes._post()
    settle()
    assert sg.replies[-1]["content"] == "Agreed."
    assert sg.replies[-1]["entity"]["id"] == thread.id, \
        "a reply to a reply belongs to the note"


def test_replies_hang_off_a_thread_line():
    from PySide6.QtWidgets import QFrame
    sg, browser = _browser_with_notes()
    rails = browser.notes.findChildren(QFrame, "threadRail")
    assert len(rails) == sum(len(t.replies) for t in browser.notes.threads)


def test_the_activity_tab_merges_notes_and_the_publish():
    sg, browser = _browser_with_notes()
    browser.tabs.setCurrentIndex(2)          # Activity
    settle()
    text = _labels(browser.activity)
    assert "published SH010_Comp_v004" in text
    assert "wrote:" in text and "replied:" in text


def test_notes_failure_is_reported_in_place():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v004")
    browser = _browser(sg)
    browser.table.selectRow(0)
    settle()

    sg.fail_find = RuntimeError("Permission denied on Note")
    browser.notes._cooldown.stop()
    browser.notes.refresh()
    settle()
    assert "Notes unavailable" in browser.notes.status.text()


# -- latest version on the task row ----------------------------------------

def test_the_task_row_shows_its_latest_version():
    from ui.software_page import TasksTable

    table = TasksTable()
    table.set_tasks([fakes.TASK])
    table.set_latest_versions(
        {fakes.TASK["id"]: {"id": 9001, "code": "SH010_Comp_v006",
                            "sg_status_list": "rev"}})
    assert table.table.item(0, TasksTable.COL_LATEST).text() == \
        "SH010_Comp_v006"


def test_a_task_with_no_versions_shows_nothing_rather_than_breaking():
    from ui.software_page import TasksTable

    table = TasksTable()
    table.set_tasks([fakes.TASK])
    table.set_latest_versions({})
    assert table.table.item(0, TasksTable.COL_LATEST).text() == ""

    menu = QMenu()
    sub = table._add_latest_version_actions(menu, fakes.TASK)
    assert sub.actions()[0].text() == "No Versions"
    assert not sub.actions()[0].isEnabled()


def test_open_latest_version_carries_the_task_and_the_version():
    from ui.software_page import TasksTable

    table = TasksTable()
    latest = {"id": 9001, "code": "SH010_Comp_v006"}
    table.set_tasks([fakes.TASK])
    table.set_latest_versions({fakes.TASK["id"]: latest})

    got = []
    table.latest_version_requested.connect(lambda t, v: got.append((t, v)))
    menu = QMenu()
    table._add_latest_version_actions(menu, fakes.TASK).actions()[0].trigger()
    assert got and got[0][0]["id"] == fakes.TASK["id"]
    assert got[0][1]["code"] == "SH010_Comp_v006"


def test_the_review_dot_says_why_it_is_there():
    from ui.software_page import TasksTable

    table = TasksTable()
    table.set_tasks([fakes.TASK])
    table.set_latest_versions(
        {fakes.TASK["id"]: {"id": 9001, "code": "SH010_Comp_v006"}})
    table.set_attention(
        {fakes.TASK["id"]: "Sam added a note on SH010_Comp_v006, 2h ago"})

    cell = table.table.item(0, TasksTable.COL_LATEST)
    assert cell.text().endswith("●")
    assert "Sam added a note" in cell.toolTip(), \
        "a dot with no explanation is noise"


# -- needs attention -------------------------------------------------------

def _review_page():
    from ui.review_page import ReviewPage

    sg = fakes.FakeShotgun()
    version = sg.add_version("SH010_Comp_v006", user=fakes.ARTIST,
                             project=fakes.PROJECT, entity=fakes.SHOT)
    sg.add_note(version["id"], "Please reduce the brightness.",
                user=fakes.CLIENT)

    page = ReviewPage(fakes.client(sg))
    page.service.read_state.path = os.path.join(TMP, "review_read.json")
    page.service.read_state.seen = {}
    page.show()
    page.refresh()
    settle()
    return sg, page, version


def test_needs_attention_lists_review_items():
    from ui.review_page import ReviewCard

    sg, page, version = _review_page()
    cards = page.findChildren(ReviewCard)
    assert len(cards) == 1
    text = _labels(page)
    assert "added a note" in text
    assert "SH010" in text and "SH010_Comp_v006" in text
    assert "reduce the brightness" in text.lower()


def test_needs_attention_is_empty_when_there_is_nothing_to_do():
    from ui.review_page import ReviewPage

    page = ReviewPage(fakes.client(fakes.FakeShotgun()))
    page.show()
    page.refresh()
    settle()
    assert page.stack.currentWidget() is page.empty


def test_opening_an_item_marks_it_read_and_navigates_to_the_version():
    from ui.review_page import ReviewCard

    sg, page, version = _review_page()
    counts = []
    page.count_changed.connect(counts.append)
    targets = []
    page.item_opened.connect(targets.append)

    page.findChildren(ReviewCard)[0].opened.emit(page.items[0])
    settle()

    assert targets and targets[0].version["id"] == version["id"], \
        "opening an item must land on the version, not on a search"
    assert page.service.read_state.is_read(targets[0])
    assert counts and counts[-1] == 0, "the unread count should drop"


def test_the_unread_count_is_reported_for_the_header():
    sg, page, version = _review_page()
    counts = []
    page.count_changed.connect(counts.append)
    page._render()
    assert counts[-1] == 1


def test_review_items_feed_the_task_dots():
    sg, page, version = _review_page()
    reasons = page.attention_by_task()
    assert fakes.TASK["id"] in reasons
    assert "added a note" in reasons[fakes.TASK["id"]]


# -- compare ---------------------------------------------------------------

def _compare(version_a, version_b, sg=None):
    from ui.version_compare import VersionCompare

    dialog = VersionCompare(fakes.client(sg or fakes.FakeShotgun()),
                            fakes.PROJECT, version_a, version_b)
    dialog.show()
    settle()
    return dialog


def _image_version(code, size=(400, 225), colour="#3d9dff"):
    path = os.path.join(TMP, f"{code}.png")
    pm = QPixmap(*size)
    pm.fill(QColor(colour))
    pm.save(path)
    return {"id": abs(hash(code)) % 10000, "code": code,
            "sg_path_to_movie": path, "user": fakes.ARTIST,
            "sg_status_list": "rev", "description": f"{code} description"}


def test_compare_any_two_versions_not_just_neighbours():
    a = _image_version("SH010_Comp_v007")
    b = _image_version("SH010_Comp_v001", colour="#f87171")
    dialog = _compare(a, b)
    text = _labels(dialog)
    assert "SH010_Comp_v007" in text and "SH010_Comp_v001" in text


def test_compare_modes_switch():
    from ui.version_compare import SIDE_BY_SIDE, AB, WIPE

    dialog = _compare(_image_version("v006"), _image_version("v005"))
    for mode in (SIDE_BY_SIDE, AB, WIPE):
        dialog.set_mode(mode)
        assert dialog.image_view.mode == mode
    assert dialog.wipe_slider.isVisible(), "wipe needs its divider"

    dialog.set_mode(AB)
    before = dialog.image_view.showing_b
    dialog.image_view.toggle()
    assert dialog.image_view.showing_b != before


def test_difference_is_offered_for_matching_stills():
    from ui.version_compare import DIFFERENCE

    dialog = _compare(_image_version("v006"),
                      _image_version("v005", colour="#34d399"))
    ok, why = dialog.image_view.difference_available()
    assert ok, why
    dialog.set_mode(DIFFERENCE)
    assert dialog.image_view.mode == DIFFERENCE
    assert dialog.image_view._difference() is not None


def test_difference_is_refused_when_resolutions_differ():
    dialog = _compare(_image_version("v006", size=(400, 225)),
                      _image_version("v005", size=(512, 288)))
    ok, why = dialog.image_view.difference_available()
    assert not ok
    assert "matching resolutions" in why
    assert dialog.image_view._difference() is None


def test_differing_resolutions_are_stated_rather_than_hidden():
    dialog = _compare(_image_version("v006", size=(400, 225)),
                      _image_version("v005", size=(512, 288)))
    text = _labels(dialog)
    assert "400 × 225" in text and "512 × 288" in text


def test_missing_media_compares_without_crashing():
    a = {"id": 1, "code": "v006", "sg_path_to_movie": "/nowhere/v006.mov"}
    b = _image_version("v005")
    dialog = _compare(a, b)
    assert dialog.media_a.error
    ok, why = dialog.image_view.difference_available()
    assert not ok and "two frames" in why


def test_movies_play_in_the_compare_view_with_a_transport():
    from ui.version_compare import WIPE
    mov = os.path.join(TMP, "SH010_comp_v006.mov")
    open(mov, "wb").write(b"\x00" * 1024)
    a = {"id": 1, "code": "v006", "sg_path_to_movie": mov}
    b = {"id": 2, "code": "v005", "sg_path_to_movie": mov}
    dialog = _compare(a, b)
    assert len(dialog.players) == 2, "one player per side"
    assert dialog.transport.isVisible()
    assert dialog.image_view.mode == WIPE, "a compare opens on the wipe"
    dialog.close()
    assert not dialog.players


def test_a_version_not_on_this_machine_streams_the_shotgrid_upload():
    from ui.version_compare import VersionMedia
    media = VersionMedia({"id": 1, "code": "v006", "sg_uploaded_movie": {
        "url": "https://sg.example/v006.mov?sig=1", "name": "v006.mov"}})
    assert media.movie == "https://sg.example/v006.mov?sig=1"
    assert media.source == media.STREAM


def test_an_expired_stream_link_is_fetched_again_once():
    from ui.version_compare import VersionMedia
    sg = fakes.FakeShotgun()
    row = sg.add_version("SH010_Comp_v006", sg_uploaded_movie={
        "url": "https://sg.example/fresh.mov", "name": "v006.mov"})
    dialog = _compare(_image_version("relink_a"), _image_version("relink_b"),
                      sg)
    media = VersionMedia(dict(row, sg_uploaded_movie={
        "url": "https://sg.example/expired.mov"}))

    class StubPlayer:
        sources = []
        def setSource(self, url): self.sources.append(url.toString())
        def play(self): pass

    player = StubPlayer()
    dialog.players.append(player)
    dialog._movie_error(media, player, "403 Forbidden")
    settle()
    assert player.sources == ["https://sg.example/fresh.mov"]
    dialog._movie_error(media, player, "403 Forbidden")
    assert "403" in media.error, "a second failure is believed"
    dialog.players.remove(player)


def test_a_cached_upload_plays_from_disk():
    import media_cache
    from ui.version_compare import VersionMedia
    url = "https://sg.example/s3/abc/AD1008_v004.mov?sig=1"
    local = media_cache.path_for(4004, url)
    os.makedirs(os.path.dirname(local), exist_ok=True)
    open(local, "wb").close()
    media = VersionMedia({"id": 4004, "code": "v004",
                          "sg_uploaded_movie": {"url": url}})
    assert media.movie == local
    assert media.source == media.CACHED
    os.remove(local)


def test_a_streaming_player_moves_onto_its_cached_copy_in_place():
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtMultimedia import QMediaPlayer
    from ui.version_compare import VersionMedia

    class StubPlayer(QObject):
        mediaStatusChanged = Signal(object)

        def __init__(self):
            super().__init__()
            self.sources, self.positions = [], []
        def position(self): return 1234
        def playbackState(self): return QMediaPlayer.PlayingState
        def setSource(self, url):
            self.sources.append(os.path.normpath(url.toLocalFile()))
        def setPosition(self, ms): self.positions.append(ms)
        def play(self): pass

    dialog = _compare(_image_version("swap_a"), _image_version("swap_b"))
    media = VersionMedia({"id": 77, "code": "v077", "sg_uploaded_movie": {
        "url": "https://sg.example/s3/v077.mov"}})
    player = StubPlayer()
    dialog.players.append(player)
    path = os.path.join(TMP, "v077.mov")
    dialog._use_cached(media, player, path)
    player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadingMedia)
    assert player.positions == [], "no seek until the copy has loaded"
    player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
    assert player.sources == [path]
    assert player.positions == [1234], "same frame, not back to the start"
    assert media.source == media.CACHED
    dialog.players.remove(player)
    dialog._use_cached(media, player, path)          # closed meanwhile
    assert player.sources == [path]


def test_a_single_version_plays_without_the_compare_modes():
    dialog = _compare(_image_version("solo_v001"), None)
    assert dialog.windowTitle() == "Version Player"
    assert not any(b.isVisible() for b in dialog.mode_buttons.buttons())
    assert not dialog.notes_switch.isVisible()
    assert _labels(dialog).count("solo_v001") >= 1


def test_dragging_on_the_picture_moves_the_wipe():
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from ui.version_compare import WIPE
    dialog = _compare(_image_version("drag_a"), _image_version("drag_b"))
    dialog.set_mode(WIPE)
    view = dialog.image_view
    view.resize(600, 300)
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(150, 150))
    assert abs(view.wipe - 0.25) < 0.01
    assert dialog.wipe_slider.value() == 25


def test_full_screen_keeps_the_picture_and_the_notes():
    dialog = _compare(_image_version("full_a"), _image_version("full_b"))
    dialog.toggle_fullscreen()
    settle(200)
    assert dialog.isFullScreen()
    assert not dialog.meta_host.isVisible()
    assert dialog.notes_panel.isVisible()
    dialog.toggle_fullscreen()
    settle(200)
    assert dialog.meta_host.isVisible()


def test_the_notes_fold_away_for_the_full_picture():
    from PySide6.QtTest import QTest
    from ui import ui_state
    dialog = _compare(_image_version("fold_a"), _image_version("fold_b"))
    width = dialog.image_view.width()
    QTest.keyClick(dialog, Qt.Key_N)
    settle(200)
    assert not dialog.notes_host.isVisible()
    assert dialog.notes_tab.isVisible(), "one click brings them back"
    assert dialog.image_view.width() > width, "the picture takes the room"
    assert ui_state.get("player_notes_hidden") is True
    dialog.notes_tab.click()
    settle(200)
    assert dialog.notes_host.isVisible() and not dialog.notes_tab.isVisible()
    assert ui_state.get("player_notes_hidden") is False


def test_notes_are_visible_while_comparing():
    sg = fakes.FakeShotgun()
    a = _image_version("SH010_Comp_v006")
    b = _image_version("SH010_Comp_v005")
    sg.add_note(a["id"], "Reduce brightness.", user=fakes.CLIENT)
    sg.add_note(b["id"], "Previous client feedback.", user=fakes.PRODUCER)

    dialog = _compare(a, b, sg)
    assert "Reduce brightness." in _labels(dialog)
    # Switching sides straight away still loads them: no refresh cooldown.
    dialog.notes_buttons[1].click()
    settle()
    text = _labels(dialog)
    assert "Previous client feedback." in text
    assert "SH010_Comp_v005" in dialog.notes_panel.title.text()


def test_compare_metadata_marks_what_differs():
    a = _image_version("v006")
    b = _image_version("v005", size=(512, 288))
    dialog = _compare(a, b)
    labels = [l for l in dialog.findChildren(QLabelType)
              if l.objectName() == "checkWarn"]
    assert labels, "a difference between the two columns should be marked"


def _seed_thumbnail(url, size=(320, 180), colour="#a78bfa"):
    """Put an image in the shared thumbnail cache, so nothing is downloaded."""
    from PySide6.QtCore import QBuffer, QByteArray
    from ui import widgets

    pm = QPixmap(*size)
    pm.fill(QColor(colour))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    pm.save(buffer, "PNG")
    buffer.close()
    widgets._thumb_cache[url] = bytes(data.data())
    return url


def test_compare_falls_back_to_the_shotgrid_thumbnail():
    """Most versions on a live site have no local media path — published by
    another tool, or on a mount this workstation does not have. Without the
    thumbnail fallback both panes are empty and compare looks broken."""
    url_a = _seed_thumbnail("https://sg.example/v006.png")
    url_b = _seed_thumbnail("https://sg.example/v005.png", colour="#34d399")
    a = {"id": 1, "code": "SH010_Comp_v006", "image": url_a}
    b = {"id": 2, "code": "SH010_Comp_v005", "image": url_b}

    dialog = _compare(a, b)
    assert not dialog.media_a.pixmap.isNull()
    assert not dialog.media_b.pixmap.isNull()
    assert dialog.media_a.source == "thumbnail"
    assert not dialog.media_a.error


def test_compare_says_which_source_is_on_screen():
    url = _seed_thumbnail("https://sg.example/v004.png")
    a = {"id": 1, "code": "v006", "image": url}
    b = {"id": 2, "code": "v005", "image": url}
    dialog = _compare(a, b)
    assert "ShotGrid thumbnail" in _labels(dialog), \
        "comparing thumbnails is useful, but must not look like the renders"


def test_side_by_side_paints_both_versions():
    from PySide6.QtGui import QImage, QPainter
    from ui.version_compare import SIDE_BY_SIDE

    a = _image_version("paint_a", colour="#3d9dff")
    b = _image_version("paint_b", colour="#f87171")
    dialog = _compare(a, b)
    dialog.set_mode(SIDE_BY_SIDE)
    view = dialog.image_view
    view.resize(600, 300)
    settle()

    image = QImage(view.size(), QImage.Format_RGB32)
    view.render(image)
    # The layout may widen the view past the resize; sample each half.
    left = image.pixelColor(view.width() // 4, 150)
    right = image.pixelColor(view.width() * 3 // 4, 150)
    assert left != right, "the two sides should not be painting the same thing"
    assert QColor("#3d9dff") in (left,) or left.blue() > left.red()
    assert right.red() > right.blue()


def test_wipe_shows_a_of_one_side_and_b_of_the_other():
    from PySide6.QtGui import QImage
    from ui.version_compare import WIPE

    a = _image_version("wipe_a", colour="#3d9dff")
    b = _image_version("wipe_b", colour="#f87171")
    dialog = _compare(a, b)
    dialog.set_mode(WIPE)
    view = dialog.image_view
    view.resize(600, 300)
    view.set_wipe(0.5)
    settle()

    image = QImage(view.size(), QImage.Format_RGB32)
    view.render(image)
    assert image.pixelColor(100, 150).blue() > image.pixelColor(100, 150).red()
    assert image.pixelColor(500, 150).red() > image.pixelColor(500, 150).blue()


def test_thumbnails_appear_beside_versions_in_the_list():
    url = _seed_thumbnail("https://sg.example/row.png")
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v001", image=url)
    browser = _browser(sg)
    settle()
    assert not browser.table.item(0, 0).icon().isNull(), \
        "the version list should show the version"


def test_a_version_with_no_thumbnail_still_lists():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v001", image=None)
    browser = _browser(sg)
    settle()
    assert browser.table.item(0, 0).text() == "SH010_Comp_v001"
    assert browser.table.item(0, 0).icon().isNull()


# -- searching every project's tasks ---------------------------------------

OTHER_PROJECT = {"id": 1400, "name": "SHOW002", "tank_name": "SHOW002"}


def _searchable():
    """A client whose artist has tasks on two different shows."""
    sg = fakes.FakeShotgun()
    fakes.add_task(sg, "Compositing", entity_name="AD1030")
    fakes.add_task(sg, "Lighting", entity_name="AD1030")
    fakes.add_task(sg, "Compositing", project=OTHER_PROJECT,
                   entity_name="XY0100")
    return sg


def test_search_matches_words_in_any_order():
    from ui.task_search import matches

    sg = _searchable()
    found = matches(sg.tasks, "ad1030 comp")
    assert len(found) == 1
    assert found[0]["content"] == "Compositing"
    assert found[0]["entity"]["name"] == "AD1030"


def test_search_reaches_tasks_on_other_projects():
    from ui.task_search import matches

    sg = _searchable()
    found = matches(sg.tasks, "xy0100")
    assert len(found) == 1
    assert found[0]["project"]["name"] == "SHOW002"


def test_search_matches_the_project_name_too():
    from ui.task_search import matches

    assert len(matches(_searchable().tasks, "show002")) == 1


def test_the_task_list_is_fetched_once_not_per_keystroke():
    from ui.task_search import TaskSearch

    search = TaskSearch()
    asked = []
    search.tasks_needed.connect(lambda: asked.append(1))
    for text in ("a", "ad", "ad1"):
        search.edit.setText(text)
    assert len(asked) == 1


def test_choosing_a_result_opens_its_project_and_selects_the_task():
    sg = _searchable()
    win = MainWindowFor(sg)
    target = [t for t in sg.tasks if t["project"]["id"] == OTHER_PROJECT["id"]][0]

    win.goto_task(target)
    settle()

    assert win.project["id"] == OTHER_PROJECT["id"], \
        "the task's own project should be the one that opened"
    assert win.task and win.task["id"] == target["id"], \
        "the task should be selected, not just visible"
    assert win.stack.currentWidget() is win.software_page


def test_a_result_is_found_even_behind_a_stale_filter():
    sg = _searchable()
    win = MainWindowFor(sg)
    win.open_project(fakes.PROJECT)
    settle()
    win.software_page.tasks.search.setText("lighting")
    settle()

    comp = [t for t in sg.tasks
            if t["project"]["id"] == fakes.PROJECT["id"]
            and t["content"] == "Compositing"][0]
    win.goto_task(comp)
    settle()
    assert win.task["id"] == comp["id"]


def test_a_selected_task_survives_the_late_arriving_columns():
    """Latest versions and review dots rebuild the table after the rows load.

    They used to clear the selection with it, which silently dropped the task
    an app would have launched against.
    """
    from ui.software_page import TasksTable

    sg = _searchable()
    table = TasksTable()
    table.set_tasks([t for t in sg.tasks
                     if t["project"]["id"] == fakes.PROJECT["id"]])
    table.table.selectRow(0)
    chosen = table._rows[0]["id"]

    table.set_latest_versions({chosen: {"id": 5, "code": "AD1030_comp_v003"}})
    assert table._selected_task_id() == chosen


# -- open tasks on the home page -------------------------------------------

def _with_finished_work():
    sg = _searchable()
    done = fakes.add_task(sg, "Roto", entity_name="AD1040")
    done["sg_status_list"] = "fin"
    return sg


def test_open_tasks_come_from_every_project_and_skip_finished_work():
    tasks = fakes.client(_with_finished_work()).open_tasks()
    assert {t["project"]["name"] for t in tasks} == {"UAT6", "SHOW002"}
    assert "Roto" not in [t["content"] for t in tasks]


def test_a_shared_task_reaches_each_person_it_names_and_nobody_else():
    sg = fakes.FakeShotgun()
    fakes.add_task(sg, "Comp", owner="ravi@5and8.ai, jitesh@5and8.ai")
    fakes.add_task(sg, "Roto", owner="Jitesh@5and8.ai;priya@5and8.ai")
    fakes.add_task(sg, "Paint", owner="xjitesh@5and8.ai")
    assert sorted(t["content"] for t in fakes.client(sg).open_tasks()) ==         ["Comp", "Roto"]


def test_the_artist_filter_splits_a_shared_task_between_its_people():
    from ui.home_tasks import HomeTasks

    owner = config.TASK_OWNER_FIELD
    home = HomeTasks(production=True)
    home.set_tasks([dict(fakes.TASK, id=1, **{owner: "Ravi, Priya"}),
                    dict(fakes.TASK, id=2, **{owner: "Priya"})])
    box = home.artist_box
    assert [box.itemText(i) for i in range(box.count())] ==         ["All artists", "Priya", "Ravi"]
    box.setCurrentIndex(box.findData("Priya"))
    assert sorted(t["id"] for t in home._rows) == [1, 2]


def test_the_done_statuses_are_filtered_by_shotgrid_not_locally():
    c = fakes.client(_with_finished_work())
    c.open_tasks()
    filters = [call[2] for call in c.sg.calls if call[1] == "Task"][-1]
    assert ["sg_status_list", "not_in", config.TASK_DONE_STATUSES] in filters


def test_home_chips_and_project_filter():
    import datetime
    from ui.home_tasks import filter_tasks

    today = datetime.date(2026, 9, 27)
    tasks = [
        dict(fakes.TASK, id=1, due_date="2026-09-20"),                 # late
        dict(fakes.TASK, id=2, due_date="2026-10-01",
             project=OTHER_PROJECT),                                   # soon
        dict(fakes.TASK, id=3, due_date=None, sg_status_list="rdy"),
        dict(fakes.TASK, id=4, due_date="2026-12-01"),                 # later
    ]
    ids = lambda **kw: [t["id"] for t in filter_tasks(tasks, today=today, **kw)]
    assert ids() == [1, 2, 3, 4]
    assert ids(chip="overdue") == [1]
    assert ids(chip="week") == [2], "overdue has its own chip"
    assert ids(chip="status:rdy") == [3]
    assert ids(project_id=OTHER_PROJECT["id"]) == [2]
    assert ids(chip="overdue", project_id=OTHER_PROJECT["id"]) == []


def test_due_dates_read_as_words():
    import datetime
    from ui.home_tasks import due_label

    today = datetime.date(2026, 9, 27)           # a Sunday
    say = lambda d: due_label({"due_date": d}, today)
    assert say("2026-09-25") == "2 days late"
    assert say("2026-09-26") == "1 day late"
    assert say("2026-09-27") == "Today"
    assert say("2026-09-28") == "Tomorrow"
    assert say("2026-10-02") == "Fri 2 Oct"
    assert say("2027-01-05") == "Tue 5 Jan 2027"
    assert say(None) == ""


def _home_with(tasks, statuses=(("ip", "In Progress"), ("rdy", "Ready"))):
    from ui.home_tasks import HomeTasks

    home = HomeTasks()
    home.set_statuses(list(statuses))
    home.set_tasks(tasks)
    return home


def test_home_sorts_by_due_with_undated_last_and_on_header_click():
    home = _home_with([
        dict(fakes.TASK, id=1, content="B", due_date=None),
        dict(fakes.TASK, id=2, content="C", due_date="2026-10-05"),
        dict(fakes.TASK, id=3, content="A", due_date="2026-09-01"),
    ])
    assert [t["id"] for t in home._rows] == [3, 2, 1]
    home._on_header_clicked(home.COL_DUE)          # descending
    assert [t["id"] for t in home._rows] == [2, 3, 1], "no date stays last"
    home._on_header_clicked(0)                     # by task name
    assert [t["content"] for t in home._rows] == ["A", "B", "C"]


def test_home_status_shows_the_label_and_chips_count():
    home = _home_with([dict(fakes.TASK, id=1, sg_status_list="ip"),
                       dict(fakes.TASK, id=2, sg_status_list="ip"),
                       dict(fakes.TASK, id=3, sg_status_list="rdy")])
    assert home.table.item(0, home.COL_STATUS).text() in ("In Progress", "Ready")
    assert home._chip_buttons["status:ip"].text() == "In Progress  2"
    assert home._chip_buttons["all"].text() == "All  3"

    home._set_chip("status:rdy")
    assert [t["id"] for t in home._rows] == [3]


def test_filters_that_hide_everything_offer_a_way_back():
    home = _home_with([dict(fakes.TASK, id=1, due_date=None)])
    home._set_chip("overdue")
    assert home.stack.currentWidget() is home.filtered_empty
    assert "Overdue" in home.filtered_title.text()
    home.clear_filters()
    assert home.stack.currentWidget() is home.table


def test_no_open_tasks_says_so():
    home = _home_with([])
    assert home.stack.currentWidget() is home.caught_up


def test_launch_button_reopens_the_last_app_used_on_the_task():
    task = dict(fakes.TASK, id=91)
    home = _home_with([task])
    button = home.table.cellWidget(0, home.COL_LAUNCH)
    assert "Open" in button.text(), "nothing launched yet: choose an app"

    home.remember_launch(task, "nuke", "16.0v7")
    button = home.table.cellWidget(0, home.COL_LAUNCH)
    assert "Nuke" in button.text()

    got = []
    home.package_launched.connect(lambda t, p, v: got.append((t["id"], p, v)))
    button.click()
    assert got == [(91, "nuke", "16.0v7")]

    again = _home_with([task])           # a restart keeps the choice
    assert "Nuke" in again.table.cellWidget(0, again.COL_LAUNCH).text()


def test_the_projects_section_folds_away_and_stays_folded():
    from ui.project_page import ProjectPage

    page = ProjectPage()
    page.toggle.setChecked(False)
    assert page.stack.isHidden()
    restarted = ProjectPage()
    assert restarted.stack.isHidden(), "the choice survives a restart"
    restarted.toggle.setChecked(True)           # leave it open for others


def test_home_shows_open_tasks_and_opens_one_on_double_click():
    sg = _with_finished_work()
    win = MainWindowFor(sg)
    home = win.home_tasks
    assert win.stack.currentWidget() is win.home
    assert home.table.rowCount() == 3, "the finished Roto task must not show"
    assert home.project_box.count() == 3     # All + two projects

    home.project_box.setCurrentIndex(home.project_box.findData(
        OTHER_PROJECT["id"]))
    assert home.table.rowCount() == 1

    home._open_row(0)
    settle()
    assert win.project["id"] == OTHER_PROJECT["id"]
    assert win.task and win.task["id"] == home._rows[0]["id"]


def test_home_menu_folders_use_the_tasks_own_project():
    from ui.home_tasks import HomeTasks

    home = HomeTasks()
    home.set_projects([fakes.PROJECT, OTHER_PROJECT])
    task = dict(fakes.TASK, project={"type": "Project",
                                     "id": OTHER_PROJECT["id"],
                                     "name": OTHER_PROJECT["name"]},
                **{"entity.Shot.sg_sequence": {"name": "SEQ001"}})
    menu = QMenu()
    home._add_folder_actions(menu, task)
    tips = [a.toolTip() for a in menu.actions()]
    assert "/jobs/SHOW002/sequences/SEQ001/shots/SH010" in tips


def test_home_menu_actions_run_in_the_tasks_project_not_the_open_one():
    import launcher

    sg = _searchable()
    win = MainWindowFor(sg)
    win.open_project(fakes.PROJECT)          # a different show is open
    settle()
    target = [t for t in sg.tasks
              if t["project"]["id"] == OTHER_PROJECT["id"]][0]

    seen = []
    original = launcher.launch_package
    launcher.launch_package = lambda project, *a, **k: (
        seen.append(project), (1, os.path.join(TMP, "x.log")))[1]
    try:
        win.home_tasks.package_launched.emit(target, "nuke", "16.0v7")
    finally:
        launcher.launch_package = original
    assert seen and seen[0]["id"] == OTHER_PROJECT["id"]


def test_marking_a_task_finished_drops_it_from_home():
    from ui.home_tasks import HomeTasks

    home = HomeTasks()
    home.set_tasks([dict(fakes.TASK, id=1), dict(fakes.TASK, id=2)])
    home.update_task(1, "fin")
    assert [t["id"] for t in home._rows] == [2]
    home.update_task(2, "hld")
    assert home._rows[0]["sg_status_list"] == "hld"


def MainWindowFor(sg):
    from ui.main_window import MainWindow

    win = MainWindow(fakes.client(sg), login="jitesh")
    settle()
    return win


# -- the signed-in user ----------------------------------------------------

def test_a_tile_thumbnail_keeps_its_download_alive():
    """The wrapper must outlive the download.

    A _ThumbJob started without a Python reference is collected while its
    thread still runs, and the emit then lands on a freed QObject — a segfault
    minutes later, on whatever the artist clicked next.
    """
    from ui import widgets

    url = "https://sg.example/tile-not-cached.png"
    widgets._thumb_cache.pop(url, None)
    started = []
    original = widgets._pool.start
    widgets._pool.start = lambda job: started.append(job)
    try:
        widgets.Tile("Some Project", image_url=url)
        assert started, "the tile should have started a fetch"
        assert started[0] in widgets._thumb_jobs, \
            "the job wrapper must be held while the download runs"
    finally:
        widgets._pool.start = original
        widgets._thumb_jobs.clear()


def test_a_thumbnail_arriving_after_its_widget_died_is_dropped():
    from ui import widgets

    url = _seed_thumbnail("https://sg.example/late.png")

    def dead(pixmap):
        raise RuntimeError("Internal C++ object already deleted.")

    widgets.load_thumbnail(url, dead)      # must not propagate


def test_the_header_shows_the_signed_in_user():
    from ui.widgets import UserChip

    chip = UserChip("Jitesh Ghase", avatar_text="jitesh@5and8.ai")
    assert chip.name_lbl.text() == "Jitesh Ghase"
    assert chip.avatar.text() == "JI"

    chip.set_name("Jitesh G", avatar_text="jghase@5and8.ai")
    assert chip.name_lbl.text() == "Jitesh G"
    assert chip.avatar.text() == "JG"


def test_the_logo_drops_its_white_ground_and_takes_the_theme_colour():
    """The asset is black on opaque white; untinted it is a white brick."""
    from ui import branding, theme

    img = branding.logo_pixmap(120).toImage()
    alphas = [img.pixelColor(x, y).alpha()
              for y in range(img.height()) for x in range(img.width())]
    clear = sum(1 for a in alphas if a < 40)
    assert clear > len(alphas) * 0.6         # the ground really is gone
    assert max(alphas) > 200                 # the mark itself is still solid

    ink = next(img.pixelColor(x, y)
               for y in range(img.height()) for x in range(img.width())
               if img.pixelColor(x, y).alpha() > 200)
    want = QColor(theme.TEXT)
    # Premultiplied storage rounds each channel, so this is near, not equal.
    assert all(abs(a - b) <= 4 for a, b in
               [(ink.red(), want.red()), (ink.green(), want.green()),
                (ink.blue(), want.blue())])


def test_the_splash_holds_its_minimum_even_when_startup_was_instant():
    """A splash that flickers past on a warm cache is worse than none."""
    from ui.branding import Splash, _fade_in

    assert _fade_in(400, 800, 900) == 0.0        # before the stage starts
    assert 0 < _fade_in(1200, 800, 900) < 1.0    # mid-stage
    assert _fade_in(9000, 800, 900) == 1.0       # clamped after

    splash = Splash("Loading Pipeline")
    splash.MIN_MS = 10 ** 6                      # startup can never beat this
    splash.show()
    splash.finish(None)
    assert splash._ready and not splash._closing

    splash.MIN_MS = 0
    splash._maybe_close()
    assert splash._closing
    splash.close()


def test_tasks_show_the_pulsing_logo_until_the_query_answers():
    """An empty table during the wait reads as "no tasks", which is a lie."""
    from ui.software_page import TasksTable

    table = TasksTable()
    table.set_loading()
    assert table.stack.currentWidget() is table.loading

    # A filter keystroke mid-flight must not swap the wait for an empty state.
    table.search.setText("comp")
    assert table.stack.currentWidget() is table.loading

    table.set_tasks([])
    assert table.stack.currentWidget() is table.empty


def test_the_profile_menu_says_who_and_how():
    win = _window()
    rows = dict(win._user_details())
    assert rows["Login"] == "jitesh"
    assert rows["Name"] == "Jitesh Ghase"
    assert rows["Domain"] == "5and8.net"
    assert "Workstation login" in rows["Signed in with"]


def test_the_profile_menu_names_the_shotgrid_user():
    win = _window()
    assert dict(win._user_details())["ShotGrid"] == fakes.ARTIST["name"]

    win.owner = None
    assert "no" in dict(win._user_details())["ShotGrid"].lower(), \
        "an unmatched HumanUser is the first thing to explain, not to hide"


def test_the_window_falls_back_to_the_os_user_without_auth():
    win = _window(auth=False)
    assert win.login, "the window must still be constructible without auth"


# -- integration -----------------------------------------------------------

def test_browser_compare_menu_offers_previous_latest_and_pick():
    sg = fakes.FakeShotgun()
    for code in ("SH010_Comp_v001", "SH010_Comp_v002", "SH010_Comp_v003"):
        sg.add_version(code)
    browser = _browser(sg)
    menu = QMenu()
    middle = browser._versions[1]            # v002
    sub = browser._add_compare_actions(menu, middle)
    texts = [a.text() for a in sub.actions() if a.text()]
    assert any("previous" in t.lower() for t in texts)
    assert any("latest" in t.lower() for t in texts)
    assert any("Compare With" in t for t in texts)


def test_browser_previous_version_uses_timestamps():
    sg = fakes.FakeShotgun()
    for code in ("SH010_Comp_v010", "SH010_Comp_v011", "SH010_Comp_v002"):
        sg.add_version(code)
    browser = _browser(sg)
    newest = browser._versions[0]
    assert newest["code"] == "SH010_Comp_v002", "newest by timestamp"
    assert browser._previous_version(newest)["code"] == "SH010_Comp_v011"


def test_selecting_a_version_by_id_lands_on_it():
    sg = fakes.FakeShotgun()
    for code in ("SH010_Comp_v001", "SH010_Comp_v002", "SH010_Comp_v003"):
        sg.add_version(code)
    target = sg.versions[0]
    browser = _browser(sg)
    browser.select_version(target["id"])
    settle()
    assert browser._selected()["id"] == target["id"]


def test_publish_new_version_is_offered_from_the_browser():
    sg = fakes.FakeShotgun()
    sg.add_version("SH010_Comp_v001")
    browser = _browser(sg)
    assert browser.publish_btn.isEnabled()


# -- helpers ---------------------------------------------------------------

def _drop(widget, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    enter = QDragEnterEvent(widget.rect().center(), Qt.CopyAction, mime,
                            Qt.LeftButton, Qt.NoModifier)
    app.sendEvent(widget, enter)
    app.sendEvent(widget, QDropEvent(QPointF(widget.rect().center()),
                                     Qt.CopyAction, mime, Qt.LeftButton,
                                     Qt.NoModifier))
    app.processEvents()


def _choose(dialog, path):
    """Pick a file, run the preflight now rather than after the debounce, and
    accept the temp-directory warning the fixtures inevitably raise."""
    dialog.file_edit.setText(path)
    dialog._run_preflight()
    settle()
    if dialog.accept_warnings.isVisible():
        dialog.accept_warnings.setChecked(True)
    return dialog.report


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == \
        os.path.normcase(os.path.normpath(b))


def _window(auth=True):
    """A MainWindow on the fake ShotGrid, signed in the way SSO signs one in."""
    from auth import AuthResult, SSO
    from ui.main_window import MainWindow

    result = AuthResult(login="jitesh", display_name="Jitesh Ghase",
                        authorized=True, method=SSO,
                        domain="5and8.net") if auth else None
    win = MainWindow(fakes.client(fakes.FakeShotgun()),
                     login=result.login if result else None,
                     auth_result=result)
    settle()
    return win


def _labels(widget):
    from PySide6.QtWidgets import QLabel
    return "\n".join(l.text() for l in widget.findChildren(QLabel))


# -- task notes and the production view --------------------------------------

def test_notes_column_counts_sorts_newest_first_and_opens_on_click():
    import datetime

    home = _home_with([dict(fakes.TASK, id=1, content="A"),
                       dict(fakes.TASK, id=2, content="B"),
                       dict(fakes.TASK, id=3, content="C")])
    home.set_notes({1: (2, datetime.datetime(2026, 10, 1, 9, 0)),
                    3: (1, datetime.datetime(2026, 10, 8, 9, 0))})
    home._on_header_clicked(home.COL_NOTES)
    assert [t["id"] for t in home._rows] == [3, 1, 2], \
        "newest conversation first, tasks without notes last"
    assert home.table.item(1, home.COL_NOTES).text().startswith("✎ 2")
    assert "Add note" in home.table.item(2, home.COL_NOTES).text()

    got = []
    home.notes_requested.connect(lambda t: got.append(t["id"]))
    home._on_cell_clicked(0, home.COL_NOTES)
    home._on_cell_clicked(0, home.COL_DUE)          # other cells do nothing
    assert got == [3]


def test_start_column_shows_the_date_and_sorts_undated_last():
    home = _home_with([dict(fakes.TASK, id=1, start_date="2026-10-20"),
                       dict(fakes.TASK, id=2, start_date=None),
                       dict(fakes.TASK, id=3, start_date="2026-10-05")])
    home._on_header_clicked(home.COL_START)
    assert [t["id"] for t in home._rows] == [3, 1, 2]
    assert home.table.item(0, home.COL_START).text() == "Mon 5 Oct"
    assert home.table.item(2, home.COL_START).text() == "—"


def test_artist_filter_shows_one_artists_tasks_in_everyone_only():
    from ui.home_tasks import HomeTasks

    owner = config.TASK_OWNER_FIELD
    tasks = [dict(fakes.TASK, id=1, **{owner: {"type": "HumanUser", "id": 1,
                                               "name": "Rahul"}}),
             dict(fakes.TASK, id=2, **{owner: {"type": "HumanUser", "id": 2,
                                               "name": "Priya"}}),
             dict(fakes.TASK, id=3, **{owner: {"type": "HumanUser", "id": 1,
                                               "name": "Rahul"}})]
    home = HomeTasks(production=True)
    home.set_tasks(tasks)
    box = home.artist_box
    assert [box.itemText(i) for i in range(box.count())] == \
        ["All artists", "Priya", "Rahul"]

    box.setCurrentIndex(box.findData("Rahul"))
    assert sorted(t["id"] for t in home._rows) == [1, 3]

    home.set_scope("mine", reload=False)
    home._rebuild()
    assert box.isHidden()
    assert len(home._rows) == 3, "Mine ignores the artist pick"


def test_refresh_says_it_landed_even_when_nothing_changed():
    from ui.home_tasks import HomeTasks

    home = HomeTasks()
    home.set_tasks([fakes.TASK])
    home.refresh_btn.click()
    home.set_tasks([], failed=True)
    assert home.refresh_btn.text() == "Refresh", "a failed reload says nothing"
    home.refresh_btn.click()
    home.set_tasks([fakes.TASK])
    assert home.refresh_btn.text().startswith("✓ Refreshed")
    home.refresh_btn._flash.timeout.emit()
    assert home.refresh_btn.text() == "Refresh"


def test_home_rows_show_the_shots_thumbnail():
    import ui.home_tasks as home_tasks
    from ui.home_tasks import HomeTasks

    pixmap = QPixmap(64, 36)
    pixmap.fill(QColor("red"))
    asked = []
    saved = home_tasks.load_thumbnail
    home_tasks.load_thumbnail = lambda url, done: (asked.append(url),
                                                   done(pixmap))
    try:
        home = HomeTasks()
        home.set_tasks([dict(fakes.TASK, **{"entity.Shot.image": "http://t"}),
                        dict(fakes.TASK, id=2)])
    finally:
        home_tasks.load_thumbnail = saved
    assert asked == ["http://t"], "no image, no fetch"
    with_image = [r for r, t in enumerate(home._rows) if t["id"] != 2][0]
    assert not home.table.item(with_image, 0).icon().isNull()
    assert home.table.item(1 - with_image, 0).icon().isNull()


def test_artist_column_is_only_shown_for_everyones_tasks():
    from ui.home_tasks import HomeTasks

    assert HomeTasks().table.isColumnHidden(HomeTasks.COL_ARTIST)
    assert not HomeTasks(production=True).table.isColumnHidden(
        HomeTasks.COL_ARTIST)


def test_any_artist_can_switch_home_to_everyones_tasks():
    sg = _with_finished_work()
    fakes.add_task(sg, "Paint", entity_name="AD1050",
                   owner=fakes.PRODUCER["email"])
    for task in sg.tasks:
        task["project"] = dict(task["project"], sg_status="Active")

    from ui.main_window import MainWindow
    win = MainWindow(fakes.client(sg), login="jitesh")
    settle()
    home = win.home_tasks
    assert home.scope == "mine", "an artist opens on their own tasks"
    mine = home.table.rowCount()

    home.scope_buttons["everyone"].click()
    settle()
    assert home.heading.text() == "All Open Tasks"
    assert home.table.rowCount() == mine + 1
    assert not home.table.isColumnHidden(home.COL_ARTIST)

    home.scope_buttons["mine"].click()
    settle()
    assert home.table.rowCount() == mine
    assert home.table.isColumnHidden(home.COL_ARTIST)


def test_leads_see_the_tasks_that_name_them_as_reviewer():
    sg = fakes.FakeShotgun()
    lead = config.TASK_LEAD_FIELD
    me = {"type": "HumanUser", "id": fakes.ARTIST["id"], "name": "Jitesh"}
    fakes.add_task(sg, "Comp", owner=fakes.PRODUCER["email"])[lead] = \
        [{"type": "HumanUser", "id": 77, "name": "Priya"}, me]
    fakes.add_task(sg, "Roto", owner=fakes.PRODUCER["email"])[lead] = \
        [{"type": "Group", "id": fakes.ARTIST["id"], "name": "Same id"}]
    fakes.add_task(sg, "Paint", owner=fakes.PRODUCER["email"])

    review = fakes.client(sg).open_tasks(scope="review")
    assert [t["content"] for t in review] == ["Comp"]

    from ui.main_window import MainWindow
    win = MainWindow(fakes.client(sg), login="jitesh")
    settle()
    home = win.home_tasks
    home.scope_buttons["review"].click()
    settle()
    assert home.heading.text() == "Tasks To Review"
    assert home.table.rowCount() == 1
    assert not home.table.isColumnHidden(home.COL_ARTIST), "whose work it is"


def test_production_sees_every_artists_open_tasks_on_active_shows():
    sg = _with_finished_work()
    fakes.add_task(sg, "Paint", entity_name="AD1050",
                   owner=fakes.PRODUCER["email"])
    for task in sg.tasks:                   # only active shows are listed
        task["project"] = dict(task["project"], sg_status="Active")

    mine = fakes.client(sg).open_tasks()
    everyone = fakes.client(sg).open_tasks(scope="everyone")
    assert "Paint" not in [t["content"] for t in mine]
    assert "Paint" in [t["content"] for t in everyone]
    assert "Roto" not in [t["content"] for t in everyone], "finished stays out"

    from ui.main_window import MainWindow
    win = MainWindow(fakes.client(sg), login="rahul", production=True)
    settle()
    assert win.home_tasks.table.rowCount() == 4
    artists = {win.home_tasks.table.item(r, win.home_tasks.COL_ARTIST).text()
               for r in range(4)}
    assert fakes.PRODUCER["email"] in artists


def test_only_production_get_the_dashboard_link():
    from ui.main_window import MainWindow
    sg = fakes.FakeShotgun()
    saved, config.DASHBOARD_URL = config.DASHBOARD_URL, "http://dash:8080"
    try:
        assert not MainWindow(fakes.client(sg), login="rahul",
                              production=True).dashboard_btn.isHidden()
        assert MainWindow(fakes.client(sg), login="jitesh",
                          production=False).dashboard_btn.isHidden()
        config.DASHBOARD_URL = ""
        assert MainWindow(fakes.client(sg), login="rahul",
                          production=True).dashboard_btn.isHidden(),             "no URL configured: no button"
    finally:
        config.DASHBOARD_URL = saved
    settle()


def test_client_notes_are_coloured_apart_from_internal_ones():
    from notes_service import Message
    from ui.notes_panel import MessageCard
    from ui.task_notes import Bubble

    def message(rule):
        author = {"name": "X", "permission_rule_set": {"name": rule}}
        return Message({"id": 1, "content": "hi"}, "note", author)

    client, internal = message("Client"), message("Artist")
    assert client.from_client and not internal.from_client
    assert MessageCard(client, False).objectName() == "noteCardClient"
    assert MessageCard(internal, False).objectName() == "noteCard"
    assert Bubble(client, False).findChild(QFrame, "chatClient")
    assert Bubble(internal, False).findChild(QFrame, "chatTheirs")
    assert Bubble(client, True).findChild(QFrame, "chatMine"),         "your own note stays yours"


def test_an_unreachable_shotgrid_shows_and_backs_off():
    from ui.main_window import MainWindow
    sg = fakes.FakeShotgun()
    win = MainWindow(fakes.client(sg), login="jitesh")
    win.show()
    settle()
    normal = win._sync_timer.interval()

    sg.fail_find = RuntimeError("Unable to find the server")
    win._poll_sync()
    settle()
    assert win.offline_btn.isVisible()
    assert "Live sync paused" in win.offline_btn.text()
    assert "Unable to find the server" in win.offline_btn.toolTip()
    first = win._sync_timer.interval()
    win._poll_sync()
    settle()
    assert win._sync_timer.interval() > first > normal, "retries space out"
    assert win._sync_timer.interval() <= 300 * 1000

    sg.fail_find = None
    win.offline_btn.click()                    # Retry now
    settle()
    assert not win.offline_btn.isVisible()
    assert win._sync_timer.interval() == normal
    assert "Back online" in win.statusBar().currentMessage()


def test_the_inbox_downloads_its_movies_ahead():
    import media_cache
    from ui import review_page
    sg = fakes.FakeShotgun()
    page = review_page.ReviewPage(fakes.client(sg))
    seen = []
    real = media_cache.prefetch
    media_cache.prefetch = lambda versions, limit: seen.append(
        ([v["id"] for v in versions], limit)) or 0

    class Item:
        def __init__(self, version):
            self.version = version
    try:
        v1, v2 = {"id": 11}, {"id": 12}
        page._prefetch([Item(v1), Item(v2), Item(v1), Item(None)])
        settle()
    finally:
        media_cache.prefetch = real
    assert seen == [([11, 12], config.REVIEW_PREFETCH)], \
        "each version once, newest first"
    assert not page._prefetching


def test_live_sync_reloads_only_for_events_that_touch_the_list():
    from ui.main_window import MainWindow
    sg = fakes.FakeShotgun()
    fakes.add_task(sg, "Comp", task_id=501)
    win = MainWindow(fakes.client(sg), login="jitesh")
    settle()
    assert win._event_id == 0, "the first poll only takes the starting id"
    reloads = []
    win._load_open_tasks = lambda: reloads.append(win._event_id)

    other = {"event_type": "Shotgun_Task_Change", "entity": {"id": 999},
             "attribute_name": "sg_status_list", "meta": {}}
    win._on_sync((10, [other]))
    assert reloads == [] and win._event_id == 10

    mine = {"event_type": "Shotgun_Task_Change", "entity": {"id": 501},
            "attribute_name": "sg_status_list", "meta": {}}
    win._on_sync((11, [mine]))
    assert reloads == [11]

    given = dict(other, attribute_name=config.TASK_OWNER_FIELD,
                 meta={"new_value": "ravi@5and8.ai, " + fakes.ARTIST["email"]})
    win._on_sync((12, [given]))
    assert reloads == [11, 12]

    to_review = dict(other, attribute_name=config.TASK_LEAD_FIELD,
                     meta={"added": [{"type": "HumanUser",
                                      "id": fakes.ARTIST["id"]}]})
    win._on_sync((13, [to_review]))
    assert reloads == [11, 12, 13]
    someone_else = dict(to_review, meta={"added": [{"type": "HumanUser",
                                                    "id": 77}]})
    win._on_sync((14, [someone_else]))
    assert reloads == [11, 12, 13]


def test_task_events_since_starts_from_the_newest_event():
    import sg_client

    class SG:
        def find_one(self, *a, **k):
            return {"id": 40}

        def find(self, entity, filters, fields, **k):
            self.filters = filters
            return [{"id": 41}, {"id": 43}]

    c = sg_client.SGClient.__new__(sg_client.SGClient)
    c.sg = SG()
    assert c.task_events_since(None) == (40, [])
    last, events = c.task_events_since(40)
    assert last == 43 and len(events) == 2
    assert ["id", "greater_than", 40] in c.sg.filters


def test_review_posts_the_note_before_the_status_and_leaves_mail_to_sg():
    import status_mail
    from ui.main_window import MainWindow
    sg = fakes.FakeShotgun()
    task = fakes.add_task(sg, "Comp")
    win = MainWindow(fakes.client(sg), login="jitesh")
    settle()
    win._ask_status_note = lambda task, code: "Edge fixed"
    mailed = []
    saved = status_mail._send_email
    status_mail._send_email = lambda: lambda *a: mailed.append(a)
    try:
        win.set_task_status(task, config.REVIEW_MAIL_STATUS)
        settle()
    finally:
        status_mail._send_email = saved
    order = [c[:2] for c in sg.calls if c[0] in ("create", "update")]
    assert order == [("create", "Note"), ("update", "Task")], order
    assert mailed == [], "the SG daemon mails the leads, not Flow"
    assert "mailed by ShotGrid" in win.statusBar().currentMessage()


def test_task_notes_chat_posts_a_signed_note_and_shows_it_as_mine():
    from ui.task_notes import TaskNotesDialog

    sg = fakes.FakeShotgun()
    client = fakes.client(sg)
    client.login = "jitesh"
    sg.create("Note", {"content": "[rahul] Please check frame 1012",
                       "tasks": [{"type": "Task", "id": fakes.TASK["id"]}],
                       "user": fakes.PRODUCER})

    dialog = TaskNotesDialog(client, fakes.PROJECT, fakes.TASK)
    settle()
    assert [m.author_name for m in dialog.messages] == ["rahul"]

    posted = []
    dialog.posted.connect(lambda: posted.append(True))
    dialog.compose.setPlainText("Fixed in v005")
    dialog.send()
    settle()
    settle()           # the post, then the reload it starts
    assert posted
    assert sg.notes[-1]["content"] == "Jitesh: Fixed in v005"
    assert sg.notes[-1]["tasks"] == [{"type": "Task", "id": fakes.TASK["id"]}]
    mine = [m for m in dialog.messages if dialog.service.can_modify(m)]
    assert [m.content for m in mine] == ["Fixed in v005"]
    assert dialog.compose.toPlainText() == ""


def test_task_notes_show_the_shots_notes_too_tagged_and_filterable():
    import notes_service
    from ui.task_notes import TaskNotesDialog, SHOT, TASK, ALL

    sg = fakes.FakeShotgun()
    client = fakes.client(sg)
    shot = {"type": "Shot", "id": fakes.SHOT["id"]}
    sg.create("Note", {"content": "Rahul: Edge on frame 1012",
                       "tasks": [{"type": "Task", "id": fakes.TASK["id"]}],
                       "note_links": [shot], "user": fakes.PRODUCER})
    sg.create("Note", {"content": "Rahul: Light is too warm",
                       "tasks": [{"type": "Task", "id": 999,
                                  "name": "Lighting"}],
                       "note_links": [shot], "user": fakes.PRODUCER})
    sg.create("Note", {"content": "Sam: Whole shot reads dark",
                       "note_links": [shot], "user": fakes.CLIENT})
    sg.create("Note", {"content": "Rahul: Not this shot",
                       "note_links": [{"type": "Shot", "id": 1}],
                       "user": fakes.PRODUCER})

    dialog = TaskNotesDialog(client, fakes.PROJECT, fakes.TASK)
    settle()
    got = [(m.content, m.scope, m.where) for m in dialog.messages]
    assert got == [("Edge on frame 1012", TASK, ""),
                   ("Light is too warm", SHOT, "SH010 · Lighting"),
                   ("Whole shot reads dark", SHOT, "SH010")], got
    assert dialog.filter_buttons[SHOT].text() == "Shot  2"

    from ui.task_notes import Bubble

    def bubbles():
        # The layout, not findChildren: the last filter's bubbles linger
        # until Qt gets round to deleteLater.
        items = (dialog.host_lay.itemAt(i).widget()
                 for i in range(dialog.host_lay.count()))
        return [w for w in items if isinstance(w, Bubble)]

    assert len(bubbles()) == 3
    dialog.set_filter(TASK)
    settle()
    assert len(bubbles()) == 1
    dialog.set_filter(SHOT)
    settle()
    assert len(bubbles()) == 2
    dialog.set_filter(ALL)

    summary = notes_service.NotesService(client).task_summary(
        [fakes.TASK, dict(fakes.TASK, id=7700, entity=None)])
    assert summary[fakes.TASK["id"]][0] == 3, summary
    assert 7700 not in summary
