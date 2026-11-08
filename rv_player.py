"""Hand Versions to OpenRV, the studio's review player.

Flow's built-in QMediaPlayer is fine for a glance at a preview movie, but it
cannot play an EXR sequence, has no colour management, and has no compare. RV
does all three, and it is already on the farm as a rez package:

    /software/packages/dcc/openrv/3.1.0/platform-linux/os-rocky-9.6/bin/rv

so it is launched the same way every other DCC here is -- `rez env openrv -- rv`
-- rather than by pointing at rv.bin, which skips the wrapper that sets RV_HOME
and the OCIO config with it.

Two things have to be taken off the environment first. RV ships its own Qt and
its own Python, and Flow is a PySide6 app whose process environment names
Flow's copies of both; a PyInstaller build makes that worse by putting its
bundled libraries on LD_LIBRARY_PATH. RV notices one of them itself --

    warning: QT_PLUGIN_PATH is set, which can cause RV to load the wrong Qt
    libraries/plugins.  Unsetting...

-- but not the rest, so they are stripped here. See HOSTILE_VARS.
"""

import glob
import json
import os
import shutil
import subprocess

import applog
import config
import context
import rez_scan
from env_resolver import build_env
from launcher import LaunchError, outlive_flow, _quote

log = applog.get()


# Variables that name Flow's Qt/Python rather than RV's. Inherited into RV they
# range from the warning above to RV refusing to start, and none of them mean
# anything to it: RV resolves its own from RV_HOME.
HOSTILE_VARS = (
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QML_IMPORT_PATH",
    "PYTHONPATH",       # Flow puts flow_dcc here; there is no RV adapter
    "PYTHONHOME",
    "LD_LIBRARY_PATH",  # set by the PyInstaller bootloader, points at Flow
    "LD_PRELOAD",
)

# Compare mode -> RV flag. Keys are version_compare's own mode constants, so
# the dialog can pass its current mode straight through.
# ponytail: flags verified against RV 3.1.0's --help on the farm; if a future
# RV renames one, this dict is the only thing to edit.
MODES = {
    "": [],
    "side": ["-tile"],          # side by side
    "ab": ["-over"],            # one stack, toggled with the ( ) keys
    "wipe": ["-wipe"],
    "difference": ["-diff"],
}

# Who is reviewing, for the notes RV sends: {"login": ..., "user": HumanUser}.
# Set by MainWindow, like SGClient.login.
reviewer = {}

# A frames path is a pattern (file.%04d.exr, file.####.exr, file.@@@@.exr),
# so os.path.isfile is always False for it. RV expands it itself.
_PATTERN_CHARS = ("%0", "#", "@")


def media_path(version):
    """The version's media for RV: the movie if there is one, else the frames.

    Frames are preferred by a colourist and the movie by everyone else, but the
    movie is what the version is reviewed against and what always exists, so it
    wins. Returns "" when neither is readable from this machine.
    """
    for field in ("sg_path_to_movie", "sg_path_to_frames"):
        path = (version.get(field) or "").strip()
        if path and readable(path):
            return path
    return ""


def readable(path):
    """Is this path something RV could open from here?

    A sequence pattern names no file that exists, so its directory is what gets
    checked -- the mount being absent is the failure worth catching, and RV
    reports an empty sequence better than a guess here would.
    """
    if not path:
        return False
    if any(c in os.path.basename(path) for c in _PATTERN_CHARS):
        return os.path.isdir(os.path.dirname(path) or ".")
    return os.path.isfile(path)


def playable(version):
    """True when Open in RV would have something to show."""
    return bool(media_path(version))


def executable():
    """(argv prefix, how it was found) for starting RV.

    rez first, because that is how the studio installs and versions RV and it
    is the only form that gets the package's own environment. The direct path
    is the fallback for a session with no rez on PATH -- a plain ssh login, a
    workstation mid-setup -- where the alternative is no review at all.
    """
    if config.RV_EXECUTABLE:
        return [config.RV_EXECUTABLE], "FLOW_RV"

    version = config.RV_VERSION or _newest_rez_version()
    if version is not None and shutil.which(config.REZ_EXECUTABLE):
        request = rez_scan.request(config.RV_PACKAGE, version or None)
        return ([config.REZ_EXECUTABLE, "env", request, "--",
                 rez_scan.command_for(config.RV_PACKAGE)], "rez")

    found = _find_in_package_tree()
    if found:
        log.warning("%s is not in the rez tree — falling back to %s",
                    config.RV_PACKAGE, found)
        return [found], "package tree"

    raise LaunchError(
        f"OpenRV was not found. Flow looked for the rez package "
        f"'{config.RV_PACKAGE}' under {config.DCC_PACKAGES_ROOT} and for a "
        f"binary under {config.RV_GLOB}.\n\nSet FLOW_RV to the absolute path "
        f"of the rv wrapper if it lives somewhere else.")


def _newest_rez_version():
    """Newest released RV, "" when the package exists without versions, or None."""
    for name, versions in rez_scan.scan():
        if name == config.RV_PACKAGE:
            return versions[0] if versions else ""
    return None


def _find_in_package_tree():
    """The rv wrapper inside an installed package, newest first.

    Deliberately not rv.bin: the wrapper next to it is what sets RV_HOME, and
    without RV_HOME RV starts without its own packages or its OCIO config.
    """
    matches = [p for p in glob.glob(config.RV_GLOB) if os.access(p, os.X_OK)]
    matches.sort(key=lambda p: rez_scan.version_key(_version_from_path(p)),
                 reverse=True)
    return matches[0] if matches else ""


def _version_from_path(path):
    """3.1.0 out of .../openrv/3.1.0/platform-linux/..., or "" if it is not there."""
    parts = os.path.normpath(path).split(os.sep)
    if config.RV_PACKAGE in parts:
        index = parts.index(config.RV_PACKAGE) + 1
        if index < len(parts):
            return parts[index]
    return ""


def command(paths, mode="", notes=False):
    """The full argv for reviewing these paths, compare flag included.

    notes=True loads Flow's "Send Annotations to ShotGrid" menu
    (rez/flow_dcc/startup/rv/flow_review.py) into RV.
    """
    if not paths:
        raise LaunchError("No media to open in RV.")
    prefix, _ = executable()
    flags = MODES.get(mode, [])
    if flags and len(paths) < 2:
        flags = []           # -wipe with one source is just a slower open
    return prefix + list(config.RV_ARGS) + flags + \
        (_notes_flags() if notes else []) + list(paths)


def _notes_flags():
    """-pyeval that loads flow_review from the in-DCC tools, or nothing.

    Appended, never prepended, to RV's sys.path, so nothing of Flow's can
    shadow RV's own modules. flow_dcc is on it too: flow_review uses its
    shotgun_api3 lookup.
    """
    startup = os.path.join(config.DCC_SOURCE_ROOT, "startup", "rv")
    if not os.path.isfile(os.path.join(startup, "flow_review.py")):
        log.warning("no %s -- RV opens without the Send Annotations menu",
                    startup)
        return []
    return ["-pyeval",
            f"import sys; sys.path.append({config.DCC_SOURCE_ROOT!r}); "
            f"sys.path.append({startup!r}); "
            "import flow_review; flow_review.install()"]


def review_env(versions, paths, project):
    """What flow_review needs to know: which Version each path is, and who."""
    return {
        "FLOW_RV_REVIEW": json.dumps({
            "site": config.SG_SITE,
            "project": {"type": "Project", "id": project["id"]},
            "login": reviewer.get("login") or "",
            "versions": [{"path": p, "id": v["id"], "code": v.get("code"),
                          "entity": v.get("entity"), "task": v.get("sg_task")}
                         for v, p in zip(versions, paths)],
        }, default=str),
        "FLOW_SG_API_PATH": context._sg_api_path(),
    }


def environment(project=None, software_code=None):
    """The launch environment for RV: the project's, minus Flow's own Qt/Python.

    Going through build_env is what puts the project's OCIO config and any
    per-show RV settings on it -- the same envs/<project>.yml every DCC launch
    reads -- so review matches what the artist sees in Nuke.
    """
    software = {"code": software_code or config.RV_PACKAGE}
    env = build_env(project, software) if project else dict(os.environ)
    for name in HOSTILE_VARS:
        env.pop(name, None)
    return env


def open_versions(versions, mode="", project=None):
    """Review these Versions in RV. Returns (pid, log_path).

    Versions with no media on this machine are dropped rather than passed to
    RV as missing files, which it opens as black frames with no explanation.
    Raises LaunchError when that leaves nothing.
    """
    versions = [v for v in versions if v]
    paths, found, missing = [], [], []
    for version in versions:
        path = media_path(version)
        if path:
            paths.append(path)
            found.append(version)
        else:
            missing.append(version.get("code") or f"Version {version['id']}")

    if not paths:
        raise LaunchError(
            "None of the selected versions have media this machine can read.\n"
            "\nShotGrid has no sg_path_to_movie or sg_path_to_frames for them, "
            "or the mount they live on is not available here.")
    if missing:
        log.warning("no media for %s — opening RV without them",
                    ", ".join(missing))

    notes = review_env(found, paths, project) if project else None
    return open_paths(paths, mode=mode, project=project, notes=notes)


def open_paths(paths, mode="", project=None, notes=None):
    """Same, for paths that did not come from a Version. Returns (pid, log_path).

    notes: review_env() for paths that are Versions, which adds the Send
    Annotations menu.
    """
    cmd = command(paths, mode, notes=bool(notes))
    env = environment(project)
    env.update(notes or {})
    log_path = applog.launch_log_path("rv")

    log.info("opening %d source(s) in RV%s",
             len(paths), f" ({mode})" if mode else "")
    log.info("command: %s", _quote(cmd))
    log.info("output:  %s", log_path)

    with open(log_path, "wb") as out:
        out.write(_log_header(cmd, log_path, paths, mode).encode())
        out.flush()
        try:
            proc = subprocess.Popen(
                outlive_flow(cmd, "rv"),
                env=env,
                start_new_session=True,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        except OSError as e:
            log.error("could not start RV: %s", e)
            raise LaunchError(f"Could not start RV: {e}\n\nCommand:\n"
                              f"{_quote(cmd)}")

    log.info("RV started with pid %s", proc.pid)
    return proc.pid, log_path


def _log_header(cmd, log_path, paths, mode):
    return (
        "# Flow review launch (OpenRV)\n"
        f"# {log_path}\n"
        f"# mode    : {mode or 'single'}\n"
        f"# sources :\n" + "".join(f"#   {p}\n" for p in paths) +
        f"# command : {_quote(cmd)}\n"
        f"# stripped: {', '.join(HOSTILE_VARS)}\n"
        "# ---------------- process output below ----------------\n\n"
    )
