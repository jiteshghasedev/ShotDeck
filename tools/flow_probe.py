#!/usr/bin/env python3
"""Read-only probe for the lead-feedback round 2 questions. Changes nothing.

Run on a 5and8 Rocky workstation (the one where Silhouette fails to open):

    /software/pipeline/vfx-ingest-pipeline/ingest_venv/bin/python3.11 \
        flow_probe.py [project] [shot-folder-name]

    defaults: 3cbien_Test  solheim26_op_060-src-ungraded_topaz_joueuse-v020-gamma24-png

ShotGrid needs a key in SG_SCRIPT_KEY or INGEST_SG_API_KEY (or a readable
/etc/vfx-ingest/sg.env). The key is never printed. Everything goes to the
screen and to ~/flow_probe_<host>.txt -- send that file back.
"""

import glob
import os
import re
import shutil
import socket
import subprocess
import sys

PROJECT = sys.argv[1] if len(sys.argv) > 1 else "3cbien_Test"
SHOT = sys.argv[2] if len(sys.argv) > 2 else \
    "solheim26_op_060-src-ungraded_topaz_joueuse-v020-gamma24-png"
JOBS = "/jobs"
DCC = "/software/packages/dcc"
SFX_ROOT = DCC + "/silhouette"
INGEST = "/software/pipeline/vfx-ingest-pipeline"
SITE = os.environ.get("FLOW_SITE", "https://5and8.shotgrid.autodesk.com")
SECRET = re.compile(r"KEY|PASS|TOKEN|SECRET", re.I)

OUT = os.path.expanduser("~/flow_probe_{0}.txt".format(socket.gethostname()))
_out = open(OUT, "w")


def say(text=""):
    print(text)
    _out.write(str(text) + "\n")


def section(title):
    say("\n" + "=" * 78 + "\n" + title + "\n" + "=" * 78)


def run(cmd, timeout=60):
    """A shell command's output; errors are printed, never raised."""
    say("$ " + cmd)
    try:
        r = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        text = r.stdout.decode("utf-8", "replace").rstrip()
        say(_mask(text) if text else "(no output, exit {0})".format(
            r.returncode))
    except subprocess.TimeoutExpired:
        say("(timed out after {0}s)".format(timeout))


def _mask(text):
    return re.sub(r"((?:KEY|PASS\w*|TOKEN|SECRET)\w*\s*[=:]\s*)\S+",
                  r"\1****", text, flags=re.I)


def tree(root, depth, limit=400):
    """Folders (and files, at the last level) under root, depth levels."""
    if not os.path.isdir(root):
        return say("(missing) " + root)
    say(root)
    shown = 0
    base = root.rstrip("/").count("/")
    for folder, dirs, files in os.walk(root):
        level = folder.rstrip("/").count("/") - base
        dirs.sort()
        if level >= depth:
            dirs[:] = []
        say("  " * level + os.path.basename(folder) + "/" +
            ("   [{0} files: {1}]".format(len(files), ", ".join(
                sorted(files)[:4])) if files else ""))
        shown += 1
        if shown >= limit:
            return say("  ... (cut at {0} folders)".format(limit))


def guarded(func):
    def wrapper():
        try:
            func()
        except Exception as e:
            say("!! {0} failed: {1!r}".format(func.__name__, e))
    return wrapper


# -- 1. machine ---------------------------------------------------------------

@guarded
def machine():
    section("1. Machine")
    say("host {0}  user {1}  python {2}".format(
        socket.gethostname(), os.environ.get("USER"), sys.version.split()[0]))
    run("cat /etc/redhat-release; uname -r")
    run("which rez rez-env nuke ffmpeg ffprobe 2>&1")
    run("/software/pipeline/bin/ffmpeg -version 2>&1 | head -1")
    for k in sorted(os.environ):
        if re.search(r"FLOW|REZ|NUKE|FOUNDRY|SFX|SILHOUETTE|PIPELINE|SG_", k):
            v = "****" if SECRET.search(k) else os.environ[k]
            say("env {0}={1}".format(k, v))


# -- 2. Silhouette (11.1, 11.5) ---------------------------------------------

@guarded
def silhouette():
    section("2. Silhouette rez package and install")
    run("ls -la {0} {0}/* 2>&1 | head -40".format(SFX_ROOT))
    for pkg in sorted(glob.glob(SFX_ROOT + "/*/package.py")):
        say("\n--- " + pkg)
        run("cat '{0}'".format(pkg))
    run("rez-env silhouette -- bash -c 'which sfx silhouette Silhouette; "
        "env | grep -i -E \"sfx|silhouette|^PATH=|LD_LIBRARY|PYTHON\"' 2>&1",
        timeout=120)
    run("rez-env silhouette flow_dcc -- bash -c 'which sfx; echo "
        "FLOW_DCC_ROOT=$FLOW_DCC_ROOT; env | grep -i sfx' 2>&1", timeout=120)
    # The install: binaries, the Python it ships, and its own scripts --
    # the shipped scripts show the fx API (save, load, actions, menus).
    roots = sorted(set(glob.glob(SFX_ROOT + "/*/platform-*/*")) |
                   set(glob.glob("/opt/BorisFX/*ilhouette*")) |
                   set(glob.glob("/opt/*ilhouette*")))
    for root in roots:
        say("\n--- install " + root)
        run("find '{0}' -maxdepth 3 -type f -perm -u+x | head -30".format(
            root))
        run("find '{0}' -maxdepth 4 -type d \\( -iname '*script*' -o "
            "-iname '*python*' -o -iname 'actions' -o -iname 'startup' \\) "
            "| head -30".format(root))
        run("find '{0}' -name '*.py' | head -60".format(root))
        run("grep -rhoE 'fx\\.[A-Za-z_]+\\(?' --include=*.py '{0}' "
            "| sort | uniq -c | sort -rn | head -80".format(root))
        run("grep -rlE 'SFX_SCRIPT_PATH|class .*\\(fx\\.Action\\)|"
            "addAction|saveProject|loadProject|activeProject' "
            "--include=*.py '{0}' | head -20".format(root))
        for f in sorted(glob.glob(root + "/**/actions/*.py",
                                  recursive=True))[:2]:
            say("\n--- sample action " + f)
            run("head -60 '{0}'".format(f))
    # What Flow logged when the Silhouette launch failed.
    logs = sorted(glob.glob(os.path.expanduser("~/.flow/**/*.log"),
                            recursive=True) +
                  glob.glob(os.path.expanduser("~/.flow*/*.log")),
                  key=os.path.getmtime)[-5:]
    for log in logs:
        say("\n--- Flow log " + log)
        run("grep -n -i -B2 -A8 -E 'silhouette|sfx' '{0}' | tail -60".format(
            log))


# -- 3. folders on disk (9, 10, Prep, roto) --------------------------------------

@guarded
def folders():
    section("3. Folders for project " + PROJECT)
    project = JOBS + "/" + PROJECT
    run("ls '{0}'".format(project))
    tree(project + "/sequences", 2, limit=80)
    shots = glob.glob(project + "/sequences/*/shots/" + SHOT)
    say("\n--- point 9 shot: " + (shots[0] if shots else "NOT FOUND " + SHOT))
    for shot in shots:
        tree(shot + "/elements", 4)
        run("find '{0}' -iname '*.png' | head -20".format(shot))
    run("find {0}/DataIO/Inbox/{1} {0}/DataIO/_ingested/{1} -maxdepth 4 "
        "2>/dev/null | head -60".format(JOBS, PROJECT))
    run("find {0}/DataIO -iname '*solheim26*' -maxdepth 6 2>/dev/null "
        "| head".format(JOBS))
    say("\n--- one ordinary shot, as ingest built it")
    first = sorted(glob.glob(project + "/sequences/*/shots/*"))[:1]
    for shot in first:
        tree(shot, 3)
    say("\n--- paint vs prep folders on this project's shots")
    for name in ("nuke/paint/scene", "nuke/prep/scene", "nuke/roto/scene",
                 "elements/prep", "elements/roto", "nuke/renders"):
        found = glob.glob(project + "/sequences/*/shots/*/" + name)
        say("{0:<20} {1} shots".format(name, len(found)))
    say("\n--- assets (point 10)")
    tree(project + "/assets", 3, limit=60)
    tree(project + "/Reference", 2, limit=30)


# -- 4. ShotGrid --------------------------------------------------------------

def _key():
    for var in ("SG_SCRIPT_KEY", "INGEST_SG_API_KEY"):
        if os.environ.get(var):
            return os.environ.get(var), "env " + var
    try:
        for line in open("/etc/vfx-ingest/sg.env"):
            if line.startswith("INGEST_SG_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\""), \
                    "/etc/vfx-ingest/sg.env"
    except (IOError, OSError):
        pass
    return None, None


@guarded
def shotgrid():
    section("4. ShotGrid (" + SITE + ")")
    try:
        import shotgun_api3
    except ImportError:
        return say("shotgun_api3 not importable: run this with the ingest "
                   "venv python (see the top of this file)")
    key, where = _key()
    if not key:
        return say("No key: export SG_SCRIPT_KEY=... and run again")
    say("key from " + where)
    sg = shotgun_api3.Shotgun(SITE, script_name=os.environ.get(
        "SG_SCRIPT_NAME", "SG_daemon"), api_key=key)

    project = sg.find_one("Project", [["name", "is", PROJECT]],
                          ["code", "name"]) or sg.find_one(
        "Project", [["code", "is", PROJECT]], ["code", "name"])
    say("project " + repr(project))

    say("\n--- Steps (all)")
    for s in sg.find("Step", [], ["code", "short_name", "entity_type"],
                     order=[{"field_name": "entity_type", "direction": "asc"}]):
        say("  {0:<8} {1:<24} short={2}".format(
            s.get("entity_type") or "", s.get("code"), s.get("short_name")))

    if project:
        say("\n--- Task names and steps used on " + PROJECT)
        seen = {}
        for t in sg.find("Task", [["project", "is", project]],
                         ["content", "step", "entity"]):
            key2 = (t.get("content"), (t.get("step") or {}).get("name"),
                    (t.get("entity") or {}).get("type"))
            seen[key2] = seen.get(key2, 0) + 1
        for (content, step, kind), n in sorted(seen.items(),
                                               key=lambda i: str(i[0])):
            say("  {0:<6} task={1!r:<24} step={2!r:<18} x{3}".format(
                kind or "", content, step, n))

        say("\n--- Assets on " + PROJECT)
        for a in sg.find("Asset", [["project", "is", project]],
                         ["code", "sg_asset_type"])[:60]:
            say("  {0:<30} type={1}".format(a["code"], a.get("sg_asset_type")))

        say("\n--- Shot " + SHOT)
        say(repr(sg.find_one("Shot", [["project", "is", project],
                                      ["code", "is", SHOT]],
                             ["code", "sg_sequence", "description"])))

    def values(entity, field):
        try:
            props = sg.schema_field_read(entity, field)[field]["properties"]
            return props.get("valid_values", {}).get("value")
        except Exception as e:
            return "n/a ({0})".format(e)

    say("\n--- list fields")
    for entity, field in (("Version", "sg_status_list"),
                          ("Task", "sg_status_list"),
                          ("Shot", "sg_status_list"),
                          ("Asset", "sg_asset_type")):
        say("  {0}.{1}: {2}".format(entity, field, values(entity, field)))
    for entity, field in (("Task", "task_reviewers"),
                          ("Version", "sg_uploaded_movie"),
                          ("Version", "sg_path_to_frames"),
                          ("Version", "sg_task")):
        try:
            sg.schema_field_read(entity, field)
            say("  {0}.{1}: exists".format(entity, field))
        except Exception:
            say("  {0}.{1}: MISSING".format(entity, field))
    say("\n--- PublishedFileTypes")
    say("  " + ", ".join(sorted(t["code"] for t in sg.find(
        "PublishedFileType", [], ["code"]))))
    say("\n--- LocalStorage")
    for s in sg.find("LocalStorage", [], ["code", "linux_path"]):
        say("  {0}: {1}".format(s["code"], s.get("linux_path")))


# -- 5. pipeline installs, Nuke, mail -----------------------------------------

@guarded
def pipeline():
    section("5. Pipeline installs, Nuke, mail")
    run("git -C {0} branch --show-current; git -C {0} log -3 --oneline; "
        "git -C {0} status --short | head".format(INGEST))
    run("ls {0}/flow_dcc {0}/flow_context 2>&1 | head; ls -d {0}/nuke/* "
        "2>&1 | head".format(DCC))
    run("systemctl is-active vfx-ingest-watcher vfx-ingest-worker "
        "shotgun-event-daemon 2>&1")
    run("ls /var/log/vfx-ingest {0}/log 2>&1 | head".format(INGEST))
    run("grep -h -i -E 'png|images' {0}/log/vfx-ingest/worker*.log 2>/dev/null"
        " | grep -i -E 'solheim|{1}' | tail -20".format(INGEST, PROJECT))
    run("env | grep -i -E 'foundry_license|rlm_license|nuke_' | "
        "sed 's/=.*@/=****@/'")
    try:
        socket.create_connection(("smtp01.5and8.net", 55), timeout=5).close()
        say("SMTP smtp01.5and8.net:55 reachable (nothing sent)")
    except Exception as e:
        say("SMTP smtp01.5and8.net:55 NOT reachable: {0}".format(e))
    say("mailer present: {0}".format(
        os.path.isfile(INGEST + "/services/mailer.py")))
    say("ffmpeg on PATH: {0}".format(shutil.which("ffmpeg")))


if __name__ == "__main__":
    say("flow_probe: project={0} shot={1}".format(PROJECT, SHOT))
    for step in (machine, silhouette, folders, shotgrid, pipeline):
        step()
    _out.close()
    print("\nSaved to " + OUT + " -- send this file back.")
