"""One session per work file: a lock beside the scene while a DCC has it open.

The lock is `.<scene name>.lock` in the scene's own folder on /jobs, so every
workstation sees it. It holds who, where and since when, and is created with
O_EXCL so two sessions racing for one file cannot both win.

A session holds the file it has open. Opening or saving another file moves
the lock there; exiting releases it. A crashed session leaves its lock behind:
on the same workstation a dead pid frees it, and an artist can take their own
lock over from another workstation (common asks first). Anyone else's lock
is cleared by that artist reopening the file, or by deleting the .lock file.

A session that is in use refreshes the lock's "seen" time (touch). One
left idle for IDLE_MINUTES gives its lock up itself (Nuke: Flow's idle
check), and a lock not refreshed for that long -- a crashed session on
another workstation -- counts as free. Either way the next person to open
the file gets the host's own autosave offer. A task's reviewers (leads,
supervisors) can also take over a lock that is still live; common asks.
"""

import atexit
import errno
import getpass
import json
import os
import socket
import time

from . import context

_held = set()             # normalised paths this session holds

IDLE_MINUTES = int(os.environ.get("FLOW_LOCK_IDLE_MINUTES", "60"))


class Locked(Exception):
    """Someone else has the file open. .info is their lock."""

    def __init__(self, path, info):
        self.path, self.info = path, info
        Exception.__init__(self, "{0} is open by {1}".format(
            os.path.basename(path), describe(info)))


def lock_path(path):
    folder, name = os.path.split(path)
    return os.path.join(folder, "." + name + ".lock")


def me():
    return {"user": context.get().user or getpass.getuser(),
            "host": socket.gethostname(), "pid": os.getpid()}


def describe(info):
    text = "{0} on {1} since {2}".format(info.get("user") or "someone",
                                         info.get("host") or "?",
                                         info.get("time") or "?")
    if info.get("seen"):
        text += ", last active {0}".format(
            time.strftime("%H:%M", time.localtime(info["seen"])))
    return text


def stale(info, now=None):
    """Not refreshed for IDLE_MINUTES: idle, or its session is gone. A lock
    written before heartbeats existed is never stale."""
    seen = info.get("seen")
    return bool(seen) and (now or time.time()) - seen > IDLE_MINUTES * 60


def holder(path):
    """The lock of whoever else has path open, or None when it is free.

    Free includes this session's own lock and one left by a crashed session
    on this workstation.
    """
    try:
        with open(lock_path(path)) as f:
            info = json.load(f)
    except (OSError, IOError, ValueError):
        return None
    mine = me()
    if info.get("host") == mine["host"]:
        if info.get("pid") == mine["pid"] or not _alive(info.get("pid")):
            return None
    if stale(info):
        return None
    return info


def take(path, force=False):
    """Lock path for this session. Raises Locked when someone else has it;
    force takes it anyway (the artist's own lock from a crashed session)."""
    info = holder(path)
    if info and not force:
        raise Locked(path, info)
    target = lock_path(path)
    if os.path.exists(target):            # ours, stale, or forced
        _remove(target)
    data = dict(me(), time=time.strftime("%Y-%m-%d %H:%M"), seen=time.time())
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
        raise Locked(path, holder(path) or {})   # lost a race for it
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    _held.add(_norm(path))


def touch(path):
    """This session is in use: refresh its lock's "seen" time. False when
    the lock on path is no longer this session's (given up, or taken)."""
    target = lock_path(path)
    try:
        with open(target) as f:
            info = json.load(f)
    except (OSError, IOError, ValueError):
        return False
    mine = me()
    if info.get("host") != mine["host"] or info.get("pid") != mine["pid"]:
        return False
    info["seen"] = time.time()
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        json.dump(info, f)
    os.replace(tmp, target)
    return True


def keep_only(path):
    """This session now has path open: let go of every other file."""
    for held in list(_held):
        if held != _norm(path):
            release(held)


def release(path):
    """Drop this session's lock on path. Someone else's is never touched."""
    _held.discard(_norm(path))
    try:
        with open(lock_path(path)) as f:
            info = json.load(f)
    except (OSError, IOError, ValueError):
        return
    mine = me()
    if info.get("host") == mine["host"] and info.get("pid") == mine["pid"]:
        _remove(lock_path(path))


def release_all():
    for held in list(_held):
        release(held)


atexit.register(release_all)


def _norm(path):
    return os.path.normpath(path).replace("\\", "/")


def _remove(target):
    try:
        os.remove(target)
    except OSError:
        pass


def _alive(pid):
    """Whether a pid on this workstation is still running."""
    if not isinstance(pid, int):
        return False
    if os.name != "posix":
        return True        # os.kill(pid, 0) would terminate it on Windows
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno == errno.EPERM
    return True
