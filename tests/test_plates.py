"""flow_dcc.plates: the shot's newest plate and its Nuke render path."""

import os
import sys
import tempfile

import config

sys.path.insert(0, config.DCC_SOURCE_ROOT)

from flow_dcc import plates  # noqa: E402


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


def test_the_write_path_follows_the_script_name():
    class Ctx:
        entity_root = "/jobs/uat6/sequences/SEQ001/shots/AD1030"
    path = plates.write_path(Ctx())
    assert path.startswith(Ctx.entity_root + "/nuke/renders/")
    assert path.endswith(".%04d.exr") and "[value root.name]" in path
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
            node.Class = cls
            made.append(node)
            return node
        return make
    nuke.nodes = types.SimpleNamespace(Read=maker("Read"), Write=maker("Write"))
    nuke.message = lambda text: None
    return nuke, root


def test_an_empty_shot_script_gets_the_plate_and_a_write():
    from flow_dcc.adapters import nuke as adapter
    shot = tempfile.mkdtemp()
    for f in (1001, 1050):
        _touch(shot, f"elements/plates/bg/bg.{f}.exr", 10)

    made = []
    fake, root = _fake_nuke(made)
    env = {"FLOW_ENTITY_ROOT": shot.replace("\\", "/"),
           "FLOW_ENTITY_TYPE": "Shot"}
    saved_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.modules["nuke"] = fake
    try:
        adapter.setup_empty_script()
        read, write = made
        assert read.Class == "Read" and read["file"].value().endswith(
            "/elements/plates/bg/bg.%04d.exr")
        assert (read["first"].value(), read["last"].value()) == (1001, 1050)
        assert (root["first_frame"].value(), root["last_frame"].value()) == \
            (1001, 1050)
        assert write.inputs == [read]
        assert write["file"].value().startswith(env["FLOW_ENTITY_ROOT"] +
                                                "/nuke/renders/")

        existing = list(made)
        fake.allNodes = lambda *a: existing
        made.clear()
        adapter.setup_empty_script()      # the script is no longer empty
        assert made == []
    finally:
        del sys.modules["nuke"]
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
