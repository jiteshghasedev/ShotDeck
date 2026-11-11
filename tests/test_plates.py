"""flow_dcc.plates: the shot's newest plate and its Nuke render path."""

import os
import sys
import tempfile

import config

sys.path.insert(0, config.DCC_SOURCE_ROOT)

from flow_dcc import lock, picker, plates  # noqa: E402


def _touch(root, rel, mtime):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    os.utime(path, (mtime, mtime))


def test_the_newest_sequence_is_the_plate_with_its_frame_range():
    root = tempfile.mkdtemp()
    for f in (1001, 1002, 1003):
        _touch(root, f"v001/bg.{f:04d}.exr", 100)
        _touch(root, f"v002/bg.{f:04d}.exr", 200)
    _touch(root, "v002/notes.txt", 999)
    plate = plates.find_plate(root)
    assert plate == {"path": root.replace("\\", "/") + "/v002/bg.%04d.exr",
                     "first": 1001, "last": 1003}


def test_a_movie_counts_and_no_plates_is_none():
    root = tempfile.mkdtemp()
    assert plates.find_plate(root) is None
    assert plates.find_plate(os.path.join(root, "missing")) is None
    _touch(root, "ref/plate.mov", 50)
    assert plates.find_plate(root)["path"].endswith("/ref/plate.mov")
    assert plates.find_plate(root)["first"] is None
    assert plates.find_movie(root)["path"].endswith("/ref/plate.mov")

    _touch(root, "bg/bg.1001.exr", 10)        # older than the movie
    assert plates.find_plate(root)["path"].endswith("/bg/bg.%04d.exr")
    assert plates.find_movie(root)["path"].endswith("/ref/plate.mov")


def test_the_studio_plate_layout_finds_the_sequence_and_its_movie():
    root = tempfile.mkdtemp()
    folder = "AD1060_CP01_V01/v001/"
    for f in range(1009, 1016):
        _touch(root, folder + f"UAT6_AD1008_v001.{f}.exr", 100)
    _touch(root, folder + "UAT6_AD1008_v001.mov", 200)
    base = root.replace("\\", "/") + "/" + folder
    assert plates.find_plate(root) == {
        "path": base + "UAT6_AD1008_v001.%04d.exr", "first": 1009,
        "last": 1015}
    assert plates.find_movie(root)["path"] == base + "UAT6_AD1008_v001.mov"


def test_exr_frames_beat_a_newer_jpeg_and_a_jpeg_alone_still_loads():
    root = tempfile.mkdtemp()
    for f in (1001, 1002):
        _touch(root, f"review/v002/sh.{f}.jpg", 300)
    assert plates.find_plate(root)["path"].endswith("/review/v002/sh.%04d.jpg")
    for f in (1001, 1002):
        _touch(root, f"full/v001/sh.{f}.exr", 100)
    assert plates.find_plate(root)["path"].endswith("/full/v001/sh.%04d.exr")


def test_the_write_path_follows_the_script_name():
    class Ctx:
        entity_root = "/jobs/uat6/sequences/SEQ001/shots/AD1030"
    path = plates.write_path(Ctx())
    assert path.startswith(Ctx.entity_root + "/nuke/renders/")
    assert path.endswith(".%04d.exr") and "[value root.name]" in path
    named = plates.write_path(Ctx(), "/work/sh_comp_v0002.nk")
    assert named == Ctx.entity_root + \
        "/nuke/renders/sh_comp_v0002/sh_comp_v0002.%04d.exr"
    Ctx.entity_root = ""
    assert plates.write_path(Ctx()) is None


# -- the Nuke side, against a stand-in nuke module --------------------------

class _Knob:
    def __init__(self, value=None):
        self._value = value

    def setValue(self, value):
        self._value = value

    def value(self):
        return self._value


class _Node:
    def __init__(self, inputs=(), **knobs):
        self.inputs = list(inputs)
        self._knobs = {k: _Knob(v) for k, v in knobs.items()}
        for k in ("file", "first", "last", "origfirst", "origlast", "file_type",
                  "create_directories", "first_frame", "last_frame"):
            self._knobs.setdefault(k, _Knob())
        self._knobs.setdefault("format", _Knob("HD_1080"))

    def __getitem__(self, name):
        return self._knobs[name]

    def knobs(self):
        return self._knobs

    def xpos(self):
        return 0

    def ypos(self):
        return 0

    def setXYpos(self, x, y):
        pass

    def format(self):
        return _Format(640, 480, 1.0, "")       # what a Read reports: unnamed


class _Format:
    def __init__(self, width, height, aspect, name):
        self._v = (width, height, aspect, name)

    def width(self):
        return self._v[0]

    def height(self):
        return self._v[1]

    def pixelAspect(self):
        return self._v[2]

    def name(self):
        return self._v[3]


def _fake_nuke(made):
    import types
    nuke = types.ModuleType("nuke")
    root = _Node(name="Root")
    root.name = lambda: "Root"
    nuke.root = lambda: root
    nuke.allNodes = lambda *a: list(made)

    def maker(cls):
        def make(**kw):
            node = _Node(**kw)
            node.Class = lambda: cls
            made.append(node)
            return node
        return make
    nuke.nodes = types.SimpleNamespace(Read=maker("Read"), Write=maker("Write"))
    nuke.message = lambda text: None
    nuke.formats = lambda: [_Format(1920, 1080, 1.0, "HD_1080")]
    nuke.added_formats = []
    nuke.addFormat = nuke.added_formats.append
    nuke.saved = []

    def save_as(path, overwrite=0):
        nuke.saved.append(path)
        root.name = lambda: path
    nuke.scriptSaveAs = save_as
    return nuke, root


def test_a_shot_tasks_first_script_is_built_and_saved_as_v0001():
    from flow_dcc.adapters import nuke as adapter
    shot = tempfile.mkdtemp()
    for f in (1001, 1050):
        _touch(shot, f"elements/plates/bg/bg.{f}.exr", 10)
    _touch(shot, "elements/plates/bg/bg.mov", 10)

    made = []
    fake, root = _fake_nuke(made)
    env = {"FLOW_ENTITY_ROOT": shot.replace("\\", "/"),
           "FLOW_ENTITY_TYPE": "Shot", "FLOW_ENTITY_NAME": "AD1030",
           "FLOW_TASK_ID": "42", "FLOW_STEP": "Comp",
           "FLOW_SOFTWARE": "nuke", "FLOW_USER": "jitesh",
           "FLOW_PROJECT_CODE": "UAT6", "FLOW_SEQUENCE": "SEQ001"}
    saved_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.modules["nuke"] = fake
    kept = []
    real_keep_open, adapter._keep_open = adapter._keep_open, kept.append
    real_open = adapter.open_scene
    real_ask = picker.ask_start
    try:
        adapter.setup_empty_script()
        read, movie, write = made
        assert read.Class() == "Read" and read["file"].value().endswith(
            "/elements/plates/bg/bg.%04d.exr")
        assert movie.Class() == "Read" and movie["file"].value().endswith(
            "/elements/plates/bg/bg.mov")
        work = env["FLOW_ENTITY_ROOT"] + "/nuke/comp/scene/"
        script = work + "SEQ001_AD1030_jitesh_comp_v0001.nk"
        assert fake.saved == [script, script]   # named, then with the nodes
        assert kept == [script]                 # watched while Nuke starts
        assert (read["first"].value(), read["last"].value()) == (1001, 1050)
        assert (root["first_frame"].value(), root["last_frame"].value()) == \
            (1001, 1050)
        assert root["format"].value() == "Flow_640x480"     # named, no warning
        assert fake.added_formats == ["640 480 1.0 Flow_640x480"]
        assert write.inputs == [read]
        assert write["file"].value() == env["FLOW_ENTITY_ROOT"] +             "/nuke/renders/SEQ001_AD1030_jitesh_comp_v0001/"             "SEQ001_AD1030_jitesh_comp_v0001.%04d.exr"

        fake.allNodes = lambda *a: [write]          # Version Up re-points it
        adapter.save_scene(work + "SEQ001_AD1030_jitesh_comp_v0002.nk")
        assert "/SEQ001_AD1030_jitesh_comp_v0002/" in write["file"].value()
        fake.saved.pop()

        existing = list(made)
        fake.allNodes = lambda *a: existing
        made.clear()
        adapter.setup_empty_script()      # the script is no longer empty
        assert made == []

        fake.allNodes = lambda *a: []
        root.name = lambda: "Root"                  # a fresh, untitled Nuke
        open(fake.saved[0], "w").close()
        opened = []
        adapter.open_scene = lambda path: opened.append(path) or path
        adapter._flow_started = False
        asked = []

        def ask(departments, current, files_for):
            asked.append((departments, current, files_for("comp")))
            return ("open", "comp", files_for("comp")[0]["path"])
        picker.ask_start = ask
        adapter.setup_empty_script()      # the shot has a script: the dialog
        assert made == [] and len(fake.saved) == 2
        assert opened == [script]         # opened as is, no new version
        departments, current, files = asked[0]
        assert departments == ["roto", "prep", "comp", "lighting", "asset"]
        assert current == "comp" and [f["path"] for f in files] == [script]

        lock.release_all()
        picker.ask_start = lambda *a: ("new", "roto", None)
        adapter.action_load_file()        # New, in another department
        roto = env["FLOW_ENTITY_ROOT"] + \
            "/nuke/roto/scene/SEQ001_AD1030_jitesh_roto_v0001.nk"
        assert fake.saved[2:] == [roto, roto]   # with a plate and a Write
        assert [n.Class() for n in made] == ["Read", "Read", "Write"]
        assert "/SEQ001_AD1030_jitesh_roto_v0001/" in made[2]["file"].value()
    finally:
        picker.ask_start = real_ask
        os.environ.pop("FLOW_DEPARTMENT", None)
        adapter.open_scene = real_open
        lock.release_all()
        adapter._keep_open = real_keep_open    # its timers outlive the fake
        adapter._flow_started = False
        del sys.modules["nuke"]
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
