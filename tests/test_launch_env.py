"""The tools paths a launch adds: rez first, source tree only as a fallback."""

import importlib.machinery
import os
import shutil
import tempfile

import config
import launcher
from env_resolver import build_env

MAYA_STARTUP = os.path.join(config.DCC_SOURCE_ROOT, "startup", "maya")
NUKE_STARTUP = os.path.join(config.DCC_SOURCE_ROOT, "startup", "nuke")

# What _rez_packages() returns once flow_dcc has been built and injected.
RESOLVED = ["maya-2026", "flow_context", "flow_dcc"]

TMP = tempfile.mkdtemp(prefix="flow-launch-")


def _paths(code, rez_pkgs=()):
    return launcher._tools_paths({"code": code}, rez_pkgs)


def _entries(paths, key="PYTHONPATH+"):
    return paths.get(key, "").split(os.pathsep) if paths.get(key) else []


# -- case B: no rez package, the source tree fills in ------------------------

def test_the_source_tree_is_used_when_rez_has_no_flow_package():
    entries = _entries(_paths("Maya"))
    assert entries[:2] == [config.DCC_SOURCE_ROOT, config.CONTEXT_SOURCE_ROOT]
    assert os.path.isdir(os.path.join(config.DCC_SOURCE_ROOT, "flow_dcc"))


def test_maya_gets_its_userSetup_folder_and_nuke_does_not():
    maya = _paths("Maya")
    assert MAYA_STARTUP in _entries(maya)
    assert "NUKE_PATH+" not in maya

    nuke = _paths("NukeX")
    assert nuke["NUKE_PATH+"] == NUKE_STARTUP
    assert MAYA_STARTUP not in _entries(nuke)


def test_silhouette_imports_the_flow_startup_package():
    sfx = _paths("silhouette")
    startup = sfx["SFX_SCRIPT_IMPORTS+"]
    assert startup == os.path.join(config.DCC_SOURCE_ROOT, "startup",
                                   "silhouette", "flow_startup")
    assert os.path.isfile(os.path.join(startup, "__init__.py"))
    assert _entries(sfx, "SFX_SCRIPT_PATH+")[0] == config.DCC_SOURCE_ROOT
    assert "NUKE_PATH+" not in sfx
    import rez_scan
    assert rez_scan.command_for("silhouette") == "silhouette"


def test_an_existing_pythonpath_is_kept_and_comes_last():
    project = {"id": 1, "name": "UAT6", "tank_name": "uat6"}
    env = build_env(project, {"code": "Maya"},
                    extra={"PYTHONPATH": "/studio/lib", **_paths("Maya")})
    entries = env["PYTHONPATH"].split(os.pathsep)
    assert entries[:3] == [config.DCC_SOURCE_ROOT, config.CONTEXT_SOURCE_ROOT,
                           MAYA_STARTUP]
    assert entries[-1] == "/studio/lib"


def test_launch_extras_keep_their_placeholders():
    # FLOW_ASSET_PATH_TEMPLATE is filled in DCC-side; expanding it here raised
    # KeyError('asset_type') and every launch failed.
    project = {"id": 1, "name": "UAT6", "tank_name": "uat6"}
    template = "/jobs/uat6/assets/{asset_type}/{asset}"
    env = build_env(project, {"code": "NukeX"},
                    extra={"FLOW_ASSET_PATH_TEMPLATE": template})
    assert env["FLOW_ASSET_PATH_TEMPLATE"] == template


def test_the_fallback_survives_rez_resetting_pythonpath():
    tools = _paths("Maya")
    env = {}
    launcher._keep_tools_paths_through_rez(
        [config.REZ_EXECUTABLE, "env"], env, tools)
    assert env["REZ_PARENT_VARIABLES"].split(",") == ["PYTHONPATH"]


def test_a_plain_exe_launch_needs_no_rez_variable():
    env = {}
    launcher._keep_tools_paths_through_rez(
        ["/usr/autodesk/maya/bin/maya"], env, _paths("Maya"))
    assert env == {}


# -- case A: the rez package is resolved and stays authoritative -------------

def test_nothing_is_added_when_rez_resolves_flow_dcc():
    assert _paths("Maya", RESOLVED) == {}
    assert _paths("NukeX", RESOLVED) == {}


def test_rez_launches_are_left_alone_when_the_package_is_resolved():
    env = {}
    launcher._keep_tools_paths_through_rez(
        [config.REZ_EXECUTABLE, "env"], env, _paths("Maya", RESOLVED))
    assert env == {}, "rez's own environment must not be touched"


def test_the_context_source_is_dropped_on_its_own_when_rez_has_it():
    entries = _entries(_paths("Maya", ["maya-2026", "flow_context"]))
    assert config.CONTEXT_SOURCE_ROOT not in entries
    assert config.DCC_SOURCE_ROOT in entries


# -- a DCC must outlive the launcher that started it -------------------------

def _with_scopes(available):
    """Pin the probe result, so the tests never touch the real systemd."""
    launcher._SCOPES_WORK = available
    try:
        return launcher.outlive_flow(
            [config.REZ_EXECUTABLE, "env", "maya"], "Maya")
    finally:
        launcher._SCOPES_WORK = None


def test_the_dcc_gets_a_scope_of_its_own_when_systemd_can_give_one():
    spawn = _with_scopes(True)
    assert spawn[:5] == ["systemd-run", "--user", "--scope", "--quiet",
                         "--collect"]
    assert spawn[5:] == [config.REZ_EXECUTABLE, "env", "maya"]
    # A generated unit name, so two DCCs from one Flow cannot collide.
    assert "--unit" not in spawn


def test_the_command_is_untouched_where_scopes_do_not_work():
    assert _with_scopes(False) == [config.REZ_EXECUTABLE, "env", "maya"]


# -- precedence: which flow_dcc an interpreter would actually import -----

def _fake_rez_install():
    """A released package laid out the way REZ_INSTALLER.py installs it."""
    root = os.path.join(TMP, "installed", "flow_dcc", "1.0.0", "python")
    package = os.path.join(root, "flow_dcc")
    if not os.path.isdir(package):
        os.makedirs(package)
    open(os.path.join(package, "__init__.py"), "w").close()
    return root


def _resolves_to(path_entries):
    spec = importlib.machinery.PathFinder.find_spec("flow_dcc",
                                                    path_entries)
    return spec.origin


def test_the_released_package_wins_when_both_are_on_pythonpath():
    # rez prepends its own copy onto whatever it inherited, so even if the
    # source tree is still in the environment the installed one is imported.
    installed = _fake_rez_install()
    assert _resolves_to([installed, config.DCC_SOURCE_ROOT]) == \
        os.path.join(installed, "flow_dcc", "__init__.py")


def test_the_source_tree_is_what_imports_without_a_released_package():
    assert _resolves_to([config.DCC_SOURCE_ROOT]) == \
        os.path.join(config.DCC_SOURCE_ROOT, "flow_dcc", "__init__.py")


def test_login_shell_env_adds_rez_path_without_overriding_the_session():
    saved = {k: os.environ.get(k) for k in
             ("PATH", "REZ_CONFIG_FILE", "REZ_PACKAGES_PATH")}
    try:
        os.environ["PATH"] = os.pathsep.join(["/usr/bin", "/bin"])
        os.environ["REZ_PACKAGES_PATH"] = "/mine"
        os.environ.pop("REZ_CONFIG_FILE", None)
        login_path = os.pathsep.join(["/opt/rez/bin", "/usr/bin"])
        config._merge_login_shell_env(
            b"bashrc noise\nHOME=/home/a\0"
            + f"PATH={login_path}".encode() + b"\0"
            b"REZ_CONFIG_FILE=/software/rezconfig.py\0"
            b"REZ_PACKAGES_PATH=/theirs\0")
        assert os.environ["PATH"].split(os.pathsep) == \
            ["/usr/bin", "/bin", "/opt/rez/bin"]
        assert os.environ["REZ_CONFIG_FILE"] == "/software/rezconfig.py"
        assert os.environ["REZ_PACKAGES_PATH"] == "/mine"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_without_rez_the_newest_installed_binary_is_found():
    saved = config.DCC_PACKAGES_ROOT
    root = os.path.join(TMP, "dcc")
    for version in ("2.0.0", "3.1.0", "10.0.0"):
        folder = os.path.join(root, "openrv", version, "platform-linux",
                              "os-rocky-9.6", "bin")
        os.makedirs(folder)
        open(os.path.join(folder, "rv"), "w").close()
    try:
        config.DCC_PACKAGES_ROOT = root
        newest = launcher.installed_binary("openrv", None, "rv")
        pinned = launcher.installed_binary("openrv", "3.1.0", "rv")
        missing = launcher.installed_binary("openrv", "9.9.9", "rv")
    finally:
        config.DCC_PACKAGES_ROOT = saved
    assert os.path.normpath(newest).split(os.sep)[-6:-4] ==         ["openrv", "10.0.0"]
    assert os.path.normpath(pinned).split(os.sep)[-5] == "3.1.0"
    assert missing == ""


def _fake_package(root, package, version, package_py, files):
    folder = os.path.join(root, package, version)
    variant = os.path.join(folder, "platform-linux", "os-rocky-9.6")
    for rel in files:
        path = os.path.join(variant, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
    with open(os.path.join(folder, "package.py"), "w") as fh:
        fh.write(package_py)
    return variant


def test_without_rez_a_package_alias_is_followed():
    saved = config.DCC_PACKAGES_ROOT
    root = os.path.join(TMP, "alias")
    pureref = _fake_package(
        root, "pureref", "2.1.2",
        'def commands():\n    alias("pureref", '
        '"{root}/PureRef-2.1.2_x64.Appimage")\n',
        ["PureRef-2.1.2_x64.Appimage"])
    tde = _fake_package(
        root, "3de", "8.1.0",
        'def commands():\n    env.PATH.append("{root}/bin")\n'
        '    alias("3de", "3DE4")\n',
        ["bin/3DE4"])
    try:
        config.DCC_PACKAGES_ROOT = root
        found_pureref = launcher.installed_binary("pureref", "2.1.2", "pureref")
        found_3de = launcher.installed_binary("3de", None, "DD3DE4")
    finally:
        config.DCC_PACKAGES_ROOT = saved
    assert os.path.normpath(found_pureref) == \
        os.path.join(pureref, "PureRef-2.1.2_x64.Appimage")
    assert os.path.normpath(found_3de) == os.path.join(tde, "bin", "3DE4")


def teardown_module():
    shutil.rmtree(TMP, ignore_errors=True)
