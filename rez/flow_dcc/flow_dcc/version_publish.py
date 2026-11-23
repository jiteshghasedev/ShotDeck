"""A render, published as a ShotGrid Version, in the background.

Publish in Nuke hands this a job: the rendered frames of the open script.
A separate process (version_worker.py, run by Nuke in terminal mode) then

    makes a review movie -> creates the Version -> uploads the movie -> mails

and the artist keeps working. Every step is written to the job file as it
finishes, so a crash -- of the worker, of Nuke, of the machine -- costs only
the step it was on: the job is picked up again where it stopped, by the next
Nuke this artist opens on this machine or by Flow > Retry Pending Publishes,
and never makes a second Version or a second upload.

No host module is imported here, so all of it is tested outside Nuke.
"""

import glob
import json
import os
import socket
import subprocess
import sys
import uuid

from . import context, paths

QUEUE_DIR = os.environ.get(
    "FLOW_PENDING_VERSIONS", os.path.expanduser("~/.flow/pending_versions"))
STATUS = os.environ.get("FLOW_PUBLISH_VERSION_STATUS", "rev")
LEAD_FIELD = os.environ.get("FLOW_TASK_LEAD_FIELD", "task_reviewers")
# Departments whose publishes also mail the task's reviewers (lead feedback).
MAIL_REVIEWERS = ("roto",)
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "version_worker.py")


def entry(name, frames, first, last, ctx=None):
    """The job for one render: frames is Nuke-style (name.%04d.exr)."""
    ctx = ctx or context.get()
    folder = os.path.dirname(frames)
    return {
        "name": name,
        "frames": frames,
        "first": first,
        "last": last,
        # Beside the frames' folder, inside for_approval/.
        "movie": os.path.dirname(folder) + "/" + name + ".mov",
        "project": {"type": "Project", "id": ctx.project_id},
        "entity": {"type": ctx.entity_type, "id": ctx.entity_id}
        if ctx.entity_type and ctx.entity_id else None,
        "task": {"type": "Task", "id": ctx.task_id},
        "site": ctx.site,
        "email": os.environ.get("FLOW_USER_EMAIL", ""),
        "artist": ctx.artist,
        "department": ctx.department,
        "mail_reviewers": ctx.department in MAIL_REVIEWERS,
        "done": [],                     # steps finished, in order
        "version_id": None,
        "error": "",
        "host": socket.gethostname(),
        "pid": None,
    }


# -- the job file ---------------------------------------------------------

def save(job):
    if not os.path.isdir(QUEUE_DIR):
        os.makedirs(QUEUE_DIR)
    path = job.get("_file") or os.path.join(
        QUEUE_DIR, uuid.uuid4().hex + ".json")
    job["_file"] = path
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(job, f, indent=2)
    os.replace(tmp, path)               # never a half-written job
    return path


def load(path):
    with open(path) as f:
        job = json.load(f)
    job["_file"] = path
    return job


def pending():
    found = []
    for path in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json"))):
        try:
            found.append(load(path))
        except (ValueError, OSError, IOError):
            continue
    return found


# -- the steps ------------------------------------------------------------

def run(job, sg_factory, make_movie, send_mail):
    """Do every step not yet done. Raises on the first failure, with the
    job saved: what finished stays finished."""
    steps = [
        ("movie", lambda: make_movie(job)),
        ("version", lambda: _create_version(sg(), job)),
        ("upload", lambda: sg().upload("Version", job["version_id"],
                                       job["movie"], "sg_uploaded_movie")),
        ("mail", lambda: send_mail(_recipients(sg(), job), *message(job))),
    ]
    connection = []

    def sg():
        if not connection:
            connection.append(sg_factory())
        return connection[0]

    for name, step in steps:
        if name in job["done"]:
            continue
        try:
            step()
        except Exception as e:
            job["error"] = "{0}: {1}".format(name, e)
            save(job)
            raise
        job["done"].append(name)
        job["error"] = ""
        save(job)
    os.remove(job["_file"])
    return job


def _create_version(sg, job):
    data = {
        "project": job["project"],
        "code": job["name"],
        "sg_task": job["task"],
        "sg_status_list": STATUS,
        "sg_path_to_frames": job["frames"],
        "sg_path_to_movie": job["movie"],
        "sg_first_frame": job["first"],
        "sg_last_frame": job["last"],
        "frame_range": "{0}-{1}".format(job["first"], job["last"]),
    }
    if job.get("entity"):
        data["entity"] = job["entity"]
    user = _user(sg, job["email"])
    if user:
        data["user"] = user
    job["version_id"] = sg.create("Version", data)["id"]
    save(job)                           # before anything else can fail


def _user(sg, email):
    if not email:
        return None
    found = sg.find_one("HumanUser", [["email", "is", email]], ["id"])
    return {"type": "HumanUser", "id": found["id"]} if found else None


def _recipients(sg, job):
    to = [job["email"]] if job["email"] else []
    if job.get("mail_reviewers"):
        task = sg.find_one("Task", [["id", "is", job["task"]["id"]]],
                           [LEAD_FIELD]) or {}
        ids = [u["id"] for u in task.get(LEAD_FIELD) or []
               if u.get("type") == "HumanUser"]
        if ids:
            to += [u["email"] for u in sg.find(
                "HumanUser", [["id", "in", ids]], ["email"])
                if u.get("email") and u["email"] not in to]
    return to


def link(job):
    site = (job.get("site") or "").rstrip("/")
    return "{0}/detail/Version/{1}".format(site, job["version_id"])


def message(job):
    """(subject, html) of the mail sent once the movie is on ShotGrid."""
    subject = "Published {0} ({1})".format(
        job["name"], paths.department_code(job["department"]))
    html = ("<p>{0} is published to ShotGrid with its movie.</p>"
            "<p><a href=\"{1}\">Open the Version</a></p>"
            "<p>Movie: {2}<br>Frames: {3} ({4}-{5})</p>").format(
                job["name"], link(job), job["movie"], job["frames"],
                job["first"], job["last"])
    return subject, html


def send_mail(to, subject, html):
    """The pipeline's mailer, as Flow's status mails use it."""
    if not to:
        return
    root = os.environ.get("FLOW_PIPELINE_ROOT",
                          "/software/pipeline/vfx-ingest-pipeline")
    if root not in sys.path:
        sys.path.append(root)
    from services.mailer import send_email
    send_email(to, subject, html)


# -- the worker process ---------------------------------------------------

def start(job, executable):
    """Run the job in its own process, which outlives this Nuke.

    executable is Nuke's own binary: the worker needs Nuke (in terminal
    mode) to make the movie. Returns the Popen.
    """
    save(job)
    log = os.path.splitext(job["_file"])[0] + ".log"
    with open(log, "a") as out:
        process = subprocess.Popen(
            [executable, "-t", WORKER, job["_file"]], stdout=out,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)
    job["pid"] = process.pid
    save(job)
    return process


def stalled(job):
    """A job on this machine whose worker failed or is gone: resume it."""
    if job.get("host") != socket.gethostname():
        return False
    pid = job.get("pid")
    if job.get("error") or not pid:
        return True
    if os.name != "posix":              # os.kill(pid, 0) is Ctrl+C there
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False
