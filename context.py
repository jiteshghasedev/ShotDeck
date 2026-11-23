"""ShotGrid context handed to a launched DCC.

A launch writes a small JSON file and points FLOW_CONTEXT_FILE at it. The
individual FLOW_* variables carry the same information for shell scripts and
for rez package commands() blocks that cannot parse JSON.

DCC-side code should not read these variables directly -- import the
`flow_context` rez package instead (see rez/flow_context/), so the
shape of the file can change without breaking every publish tool.
"""

import getpass
import json
import os
import socket
import time
import uuid

import config
import paths

CONTEXT_DIR = os.environ.get(
    "FLOW_CONTEXT_DIR", os.path.expanduser("~/.flow/context"))

# Contexts older than this are removed on the next launch.
CONTEXT_MAX_AGE_SECONDS = 7 * 24 * 3600


def build(project, software, task=None, login=None, email=None, artist=None):
    """Assemble the context dictionary for one launch."""
    entity = (task or {}).get("entity") or {}
    step = (task or {}).get("step") or {}
    return {
        "schema": 1,
        "created": time.time(),
        "site": config.SG_SITE,
        "host": socket.gethostname(),
        # Resolved here, where the templates live, so in-DCC tools never have
        # to carry a second copy of ENTITY_PATH_TEMPLATES.
        "entity_root": paths.entity_root(project, task) if task else "",
        # The sequence is only on the deep task field, so the DCC side cannot
        # derive it from the entity alone -- it is shown in the context panel.
        "sequence": paths._sequence(task) if task else "",
        # The Asset template with this project filled in and {asset_type} /
        # {asset} left as placeholders -- built by the same function as
        # entity_root, so the two can never disagree about /jobs.
        "asset_template": paths.entity_root(project, {
            "entity": {"type": "Asset", "name": "{asset}"},
            "entity.Asset.sg_asset_type": "{asset_type}"}) or "",
        "user": {
            "login": login or getpass.getuser(),
            "email": email or "",
            # Lowercase ShotGrid first name: what work files are named after.
            "artist": artist or "",
        },
        "project": {
            "id": project["id"],
            "name": project["name"],
            # Same fallback env_resolver uses, so the code matches the folder
            # name when a project has no tank_name set.
            "code": (project.get("tank_name")
                     or project["name"].replace(" ", "_").lower()),
        },
        "software": {
            "code": software.get("code") if software else "",
            "version": software.get("version") if software else "",
            "rez_packages": (software.get("sg_rez_packages") or "").split()
            if software else [],
        },
        "task": {
            "id": task["id"],
            "name": task.get("content") or "",
            "status": task.get("sg_status_list") or "",
            "step": step.get("name") or "",
            "entity_type": entity.get("type") or "",
            "entity_id": entity.get("id"),
            "entity_name": entity.get("name") or "",
        } if task else None,
    }


def write(ctx):
    """Write the context to disk and return its path."""
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    _prune()
    path = os.path.join(CONTEXT_DIR, f"{uuid.uuid4().hex}.json")
    with open(path, "w") as f:
        json.dump(ctx, f, indent=2)
    return path


def env(ctx, path):
    """Flatten the context into environment variables for the launched process."""
    task = ctx.get("task") or {}
    out = {
        "FLOW_CONTEXT_FILE": path,
        "FLOW_SITE": ctx["site"],
        "FLOW_USER": ctx["user"]["login"],
        "FLOW_USER_EMAIL": ctx["user"]["email"],
        "FLOW_ARTIST": ctx["user"].get("artist") or "",
        "FLOW_PROJECT_ID": str(ctx["project"]["id"]),
        "FLOW_PROJECT_NAME": ctx["project"]["name"],
        "FLOW_PROJECT_CODE": ctx["project"]["code"],
        "FLOW_SOFTWARE": ctx["software"]["code"],
        "FLOW_SOFTWARE_VERSION": ctx["software"]["version"] or "",
        # Present but empty when the app was launched with no task selected,
        # so publish tools can tell "no context" from "variable missing".
        "FLOW_TASK_ID": str(task.get("id") or ""),
        "FLOW_TASK_NAME": task.get("name") or "",
        "FLOW_STEP": task.get("step") or "",
        "FLOW_ENTITY_TYPE": task.get("entity_type") or "",
        "FLOW_ENTITY_ID": str(task.get("entity_id") or ""),
        "FLOW_ENTITY_NAME": task.get("entity_name") or "",
        # Where this task's scenes live. flow_dcc builds work paths from
        # this rather than re-deriving the templates on the DCC side.
        "FLOW_ENTITY_ROOT": ctx.get("entity_root") or "",
        "FLOW_SEQUENCE": ctx.get("sequence") or "",
        # Where any asset of this project lives, with {asset_type} and
        # {asset} left for the DCC to fill: a shot's USD stage references
        # its assets by path, and the template must not be copied DCC-side.
        "FLOW_ASSET_PATH_TEMPLATE": ctx.get("asset_template") or "",
        # Where shotgun_api3 lives, so a DCC can import it for publish
        # registration. A DCC ships its own Python with its own site-packages
        # and cannot see Flow's venv otherwise.
        "FLOW_SG_API_PATH": _sg_api_path(),
        # The pipeline's mailer (services/mailer.py): Nuke's background
        # publish mails the artist when the movie is on ShotGrid.
        "FLOW_PIPELINE_ROOT": config.PIPELINE_ROOT,
    }
    return out


def _sg_api_path():
    """The directory containing the shotgun_api3 package, or "".

    Read off the imported module rather than configured, so it follows the
    venv Flow is actually running from. In-DCC code appends this to
    sys.path -- never prepends -- so it cannot shadow a DCC's own modules.
    """
    try:
        import shotgun_api3
    except ImportError:
        return ""
    location = getattr(shotgun_api3, "__file__", "")
    if not location:
        return ""
    return os.path.dirname(os.path.dirname(os.path.abspath(location)))


def _prune():
    """Delete stale context files so ~/.flow does not grow without bound."""
    cutoff = time.time() - CONTEXT_MAX_AGE_SECONDS
    try:
        names = os.listdir(CONTEXT_DIR)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        p = os.path.join(CONTEXT_DIR, name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass   # another session may have removed it already
