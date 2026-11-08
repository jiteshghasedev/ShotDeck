"""A task's work files: listing every artist's, Load File, and the launch
that opens the artist's newest or creates their v0001."""

import json
import os
import sys
import tempfile
import types

import config

sys.path.insert(0, config.DCC_SOURCE_ROOT)

from flow_dcc import lock, paths, picker, versioning  # noqa: E402
from flow_dcc.adapters import common  # noqa: E402

ENV = {"FLOW_ENTITY_TYPE": "Shot", "FLOW_ENTITY_NAME": "AD1030",
       "FLOW_TASK_ID": "42", "FLOW_STEP": "Anim", "FLOW_SOFTWARE": "maya",
       "FLOW_USER": "jitesh", "FLOW_PROJECT_CODE": "UAT6",
       "FLOW_SEQUENCE": "SEQ001"}
STEM = "uat6_SEQ001_AD1030_anim"


def _with_shot(test):
    def run():
        shot = tempfile.mkdtemp().replace("\\", "/")
        env = dict(ENV, FLOW_ENTITY_ROOT=shot)
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            test(shot, shot + "/maya/scenes/anim")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    run.__name__ = test.__name__
    return run


def _touch(folder, name, mtime=100):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    open(path, "w").close()
    os.utime(path, (mtime, mtime))


@_with_shot
def test_every_artists_files_are_listed_and_versions_are_per_artist(shot, work):
    _touch(work, STEM + "_jitesh_v0001.ma", 100)
    _touch(work, STEM + "_rahul_v0002.ma", 200)
    _touch(work, STEM + "_rahul_v0002.ma~", 300)          # backup, not work
    _touch(work, STEM + "_jitesh_v0003.mb", 300)          # other extension
    _touch(work, "uat6_SEQ001_AD1030_layout_jitesh_v0009.ma")   # other task

    files = paths.task_files()
    assert [(f["version"], f["user"]) for f in files] == \
        [(2, "rahul"), (1, "jitesh")]
    assert files[0]["path"] == work + "/" + STEM + "_rahul_v0002.ma"
    assert [f["user"] for f in paths.artist_files()] == ["jitesh"]
    assert paths.existing_versions() == [1]               # rahul's v0002 is his
    assert versioning.next_scene_path().endswith(STEM + "_jitesh_v0002.ma")

    rows = picker.rows(files)
    assert rows[0][:2] == ("v0002", "rahul") and rows[0][4].endswith(".ma")
    assert rows[0][3] == ""                               # nobody has it open
    assert len(rows[0][2]) == len("2026-11-02 14:05")


class _Host(types.ModuleType):
    """Stand-in adapter: records what Flow asked the DCC to do."""

    def __init__(self, scene=""):
        super().__init__("host")
        self.scene, self.saved, self.opened, self.shown = scene, [], [], []
        self.told, self.answer = [], True

    def current_scene(self):
        return self.scene

    def save_scene(self, path):
        self.saved.append(path)
        return path

    def open_scene(self, path):
        self.opened.append(path)
        return path

    def pick_file(self, files):
        self.shown.append(files)
        return files[-1]["path"]

    def message(self, text):
        self.told.append(text)

    def confirm(self, text):
        self.told.append(text)
        return self.answer


@_with_shot
def test_the_first_launch_on_a_task_saves_an_empty_v0001(shot, work):
    host = _Host()
    path = common.start(host)
    assert path == work + "/" + STEM + "_jitesh_v0001.ma"
    assert host.saved == [path] and os.path.isdir(work)
    assert common.start(host) is None and len(host.saved) == 1   # once
    assert os.path.isfile(lock.lock_path(path))           # and it is held
    lock.release_all()
    assert not os.path.exists(lock.lock_path(path))


@_with_shot
def test_a_launch_opens_the_artists_newest_file_as_it_is(shot, work):
    _touch(work, STEM + "_rahul_v0003.ma", 300)
    _touch(work, STEM + "_jitesh_v0001.ma", 100)
    _touch(work, STEM + "_jitesh_v0002.ma", 200)
    host = _Host()
    common.start(host)
    assert host.saved == [] and host.shown == []          # no picker, no new version
    assert host.opened == [work + "/" + STEM + "_jitesh_v0002.ma"]

    common.load_file(host)                  # the menu entry, any artist's file
    assert [f["version"] for f in host.shown[0]] == [3, 2, 1]
    assert len(host.opened) == 2
    lock.release_all()


@_with_shot
def test_an_artist_new_to_a_task_gets_their_own_v0001(shot, work):
    _touch(work, STEM + "_rahul_v0003.ma", 300)
    host = _Host()
    path = common.start(host)
    assert path == work + "/" + STEM + "_jitesh_v0001.ma"
    assert host.saved == [path] and host.opened == [] and host.shown == []
    lock.release_all()


@_with_shot
def test_a_launch_that_opened_a_scene_is_left_alone(shot, work):
    host = _Host(scene=work + "/somebody.ma")
    assert common.start(host) is None
    assert host.saved == [] and host.shown == []


def _foreign_lock(path, user, host="ws-14", pid=4242):
    with open(lock.lock_path(path), "w") as f:
        json.dump({"user": user, "host": host, "pid": pid,
                   "time": "2026-11-02 14:05"}, f)


@_with_shot
def test_a_file_someone_else_has_open_cannot_be_opened(shot, work):
    _touch(work, STEM + "_rahul_v0001.ma")
    _touch(work, STEM + "_jitesh_v0002.ma")
    rahuls, mine = (work + "/" + STEM + n for n in
                    ("_rahul_v0001.ma", "_jitesh_v0002.ma"))
    _foreign_lock(rahuls, "rahul")

    held = picker.rows(paths.task_files())[1][3]
    assert held == "rahul on ws-14 since 2026-11-02 14:05"
    host = _Host()
    assert common.open_path(host, rahuls) is None
    assert host.opened == [] and "rahul on ws-14" in host.told[0]
    assert json.load(open(lock.lock_path(rahuls)))["user"] == "rahul"

    assert common.open_path(host, mine) == mine           # free: opens
    assert lock.holder(mine) is None and os.path.isfile(lock.lock_path(mine))

    with open(rahuls, "a"):
        pass
    os.remove(lock.lock_path(rahuls))                     # rahul closed it
    assert common.open_path(host, rahuls) == rahuls
    assert not os.path.exists(lock.lock_path(mine))       # moved with us
    lock.release_all()


@_with_shot
def test_my_own_lock_from_another_machine_can_be_taken_over(shot, work):
    _touch(work, STEM + "_jitesh_v0001.ma")
    path = work + "/" + STEM + "_jitesh_v0001.ma"
    _foreign_lock(path, "jitesh")

    host = _Host()
    host.answer = False                                   # "no, leave it"
    assert common.open_path(host, path) is None and host.opened == []
    host.answer = True
    assert common.open_path(host, path) == path
    assert json.load(open(lock.lock_path(path)))["host"] == lock.me()["host"]
    lock.release_all()


@_with_shot
def test_a_crashed_session_on_this_machine_does_not_block(shot, work):
    if os.name != "posix":
        return                     # no safe liveness check on Windows
    _touch(work, STEM + "_rahul_v0001.ma")
    path = work + "/" + STEM + "_rahul_v0001.ma"
    _foreign_lock(path, "rahul", host=lock.me()["host"], pid=2 ** 22 + 7)
    assert lock.holder(path) is None
    assert common.open_path(_Host(), path) == path
    lock.release_all()
