"""flow_dcc's Silhouette adapter against a stand-in `fx` module."""

import os
import sys
import tempfile
import types

import config

sys.path.insert(0, config.DCC_SOURCE_ROOT)

from flow_dcc import lock, plates, version_publish  # noqa: E402
from flow_dcc.adapters import common  # noqa: E402


def _touch(root, rel):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


class _Prop:
    def __init__(self, value):
        self.value = value


class _Source:
    def __init__(self, path):
        self._path, self.label = path, ""

    def property(self, name):
        return _Prop(self._path)


def _fake_fx():
    fx = types.ModuleType("fx")
    fx.gui = True
    fx.saved, fx.loaded, fx.shown, fx.exported = [], [], [], []

    class Project:
        def __init__(self):
            self.path, self.sources, self.is_modified = "", [], False

        def addItem(self, item):
            self.sources.append(item)

        def save(self, path=None):
            # A .sfx project is a folder holding project.sfx.
            target = path or os.path.dirname(self.path)
            os.makedirs(target, exist_ok=True)
            open(os.path.join(target, "project.sfx"), "w").close()
            self.path = target.replace("\\", "/") + "/project.sfx"
            fx.saved.append(target)

    fx.Project, fx.Source = Project, _Source
    fx.state = {"project": None}
    fx.activeProject = lambda: fx.state["project"]
    fx.setActiveProject = lambda p: fx.state.update(project=p)

    def load(path):
        fx.loaded.append(path)
        project = Project()
        project.path = path + "/project.sfx"
        fx.state["project"] = project
    fx.loadProject = load
    fx.displayInformation = lambda text, title=None: fx.shown.append(text)
    fx.askQuestion = lambda *a: True
    fx.selection = lambda: ["shape1"]

    class Exporter:
        can_export = True

        def __init__(self, name):
            self.name = name

        def export(self, path):
            fx.exported.append((self.name, path))
    fx.io_modules = {n: Exporter(n) for n in
                     ("Nuke 9+ Shapes", "Silhouette Shapes",
                      "Nuke 6.2+ Shapes")}
    return fx


def test_a_sequence_is_named_the_way_silhouette_reads_it():
    assert plates.bracket_range("/p/bg.%04d.exr", 1001, 1050) == \
        "/p/bg.[1001-1050].exr"
    assert plates.bracket_range("/p/bg.%d.png", 1, 12) == "/p/bg.[1-12].png"
    assert plates.bracket_range("/p/bg.mov", None, None) == "/p/bg.mov"


def test_a_roto_project_is_built_published_and_its_splines_exported():
    from flow_dcc.adapters import silhouette as adapter
    shot = tempfile.mkdtemp().replace("\\", "/")
    for f in (1001, 1002):
        _touch(shot, "elements/plates/bg/bg.{0}.exr".format(f))
    env = {"FLOW_ENTITY_ROOT": shot, "FLOW_ENTITY_TYPE": "Shot",
           "FLOW_ENTITY_NAME": "AD1030_v001", "FLOW_TASK_ID": "42",
           "FLOW_STEP": "Roto", "FLOW_SOFTWARE": "silhouette",
           "FLOW_USER": "jitesh", "FLOW_ARTIST": "jitesh",
           "FLOW_PROJECT_NAME": "UAT6", "FLOW_TASK_NAME": "Roto",
           "FLOW_USER_EMAIL": "jitesh@5and8.ai"}
    saved_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    fx = _fake_fx()
    sys.modules["fx"] = fx
    started, watched = [], []
    real_start, real_watch = version_publish.start, common.watch_publish
    version_publish.start = lambda job, runner, tool: started.append(
        (job, runner, tool)) or "process"
    common.watch_publish = lambda *a: watched.append(a)
    version_publish.QUEUE_DIR = tempfile.mkdtemp()
    try:
        path = adapter._start()
        name = "AD1030_jitesh_roto_v0001"
        work = shot + "/nuke/roto/scene/jitesh/" + name + ".sfx"
        assert path == work and adapter.current_scene() == work
        assert os.path.isfile(work + "/project.sfx")
        sources = fx.activeProject().sources
        assert [s._path for s in sources] == [
            shot + "/elements/plates/bg/bg.[1001-1002].exr"]

        # Rendered where Render Path says, then published.
        frames = os.path.dirname(plates.write_path(adapter.context.get(),
                                                   work))
        assert frames == shot + "/nuke/roto/renders/{0}/for_approval/{0}" \
            .format(name)
        _touch(frames, name + ".1001.exr")
        job = adapter.action_publish()
        assert job["name"] == name
        (queued, runner, tool), = started
        assert tool == "ffmpeg" and "python3" in runner[0]
        roto = shot + "/elements/roto/" + name
        assert queued["frames"] == "{0}/for_approval/{1}/{1}.%04d.exr" \
            .format(roto, name)
        assert os.path.isfile(roto + "/workfile/" + name +
                              ".sfx/project.sfx")
        assert os.path.isfile(shot + "/nuke/roto/publish/" + name +
                              ".sfx/project.sfx")
        assert "Publishing " + name in fx.shown[-1]

        class _Widgets:
            class QInputDialog:
                @staticmethod
                def getItem(*a):
                    return "Nuke 9+ Shapes", True
        real_qt, adapter._qt = adapter._qt, lambda: (None, _Widgets)
        real_window, adapter._main_window = adapter._main_window, \
            lambda: None
        try:
            adapter.export_splines()
        finally:
            adapter._qt, adapter._main_window = real_qt, real_window
        assert fx.exported == [("Nuke 9+ Shapes", shot +
                                "/nuke/roto/splines/" + name + ".nk")]
    finally:
        version_publish.start, common.watch_publish = real_start, real_watch
        adapter._flow_started = False
        lock.release_all()
        del sys.modules["fx"]
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
