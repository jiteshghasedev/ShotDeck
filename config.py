import os
import shlex
import shutil
import subprocess
import sys

SG_SITE = os.environ.get("SG_SITE", "https://5and8.shotgrid.autodesk.com")
SG_SCRIPT_NAME = os.environ.get("SG_SCRIPT_NAME", "SG_daemon")
# No default: a script key must never live in the source of a public repository.
SG_SCRIPT_KEY = os.environ.get("SG_SCRIPT_KEY", "")

# Custom assignment field on Task. Studio uses this instead of task_assignees.
TASK_OWNER_FIELD = "sg_assigned_to"

# sg_assigned_to is a plain String field on this site, NOT an entity link
# (confirmed 2026-08-08: passing an entity dict fails with
# "expected [String, NilClass] data type(s) but got Hash").
# Set True only if a site changes it to a real entity link field.
TASK_OWNER_IS_ENTITY = False

# Entity type looked up to identify the user. Change to "CustomEntity03" etc.
# if the studio does not use HumanUser.
TASK_OWNER_ENTITY = "HumanUser"
# Field on that entity matched against the current user's email address.
TASK_OWNER_MATCH_FIELD = "email"   # for CustomEntity use e.g. "sg_email"

# String mode only: which field of the resolved owner entity holds the value
# that Task.sg_assigned_to actually stores. Set to "email" if tasks store
# addresses, "name" if they store "Firstname Lastname", "login" for logins.
TASK_OWNER_STRING_FIELD = "email"
# One task can name several people, separated by commas or semicolons
# ("a@5and8.ai, b@5and8.ai"); it is then on each of their lists.

# Email domain used to derive the current user's address from their OS login
# (jitesh -> jitesh@5and8.ai). Override the whole address with SGDESK_USER_EMAIL
# when the Linux account name doesn't match the ShotGrid address.
USER_EMAIL_DOMAIN = os.environ.get("SGDESK_EMAIL_DOMAIN", "5and8.ai")


def current_user_email(login):
    """Address to match against TASK_OWNER_MATCH_FIELD for this session."""
    return (os.environ.get("SGDESK_USER_EMAIL") or f"{login}@{USER_EMAIL_DOMAIN}").strip()

TASK_FIELDS = [
    "content", "sg_status_list", "start_date", "due_date",
    "entity", "step", "project", TASK_OWNER_FIELD,
    # Deep fields, needed to build the folder path without a second query.
    "entity.Shot.sg_sequence",
    "entity.Asset.sg_asset_type",
    # The shot's or asset's thumbnail, beside the task on the home page.
    "entity.Shot.image", "entity.Asset.image",
]

# Task statuses that mean the work is finished. The home page lists every
# other task assigned to the artist. Short codes -- check them against the
# site's own Task status list.
TASK_DONE_STATUSES = [
    s for s in os.environ.get(
        "FLOW_TASK_DONE_STATUSES", "fin,cmpt,apr,omt,na").split(",")
    if s.strip()
]

# Where a task's files live. Tokens: {project} (tank_name), {project_name},
# {sequence}, {shot}, {asset}, {asset_type}, {entity}, {step}.
ENTITY_PATH_TEMPLATES = {
    "Shot": "/jobs/{project}/sequences/{sequence}/shots/{shot}",
    "Asset": "/jobs/{project}/assets/{asset_type}/{asset}",
}

# Shortcuts listed under "Open folder". {step} is the pipeline step lowercased.
# Missing ones are greyed out rather than hidden, so it's obvious when a shot
# was never built out properly.
ENTITY_SUBFOLDERS = [
    ("maya scenes",     "maya/scenes"),
    ("maya scenes / {step}", "maya/scenes/{step}"),
    ("houdini hip",     "houdini/hip"),
    ("nuke comp",       "nuke/comp/scene"),
    ("nuke / {step}",   "nuke/{step}/scene"),
    ("blender scenes",  "blender/scenes"),
    ("3DE scene",       "3DE/scene"),
    ("plates",          "elements/plates"),
    ("elements",        "elements"),
    ("dailies",         "elements/dailies"),
    ("renders",         "nuke/renders"),
]

# Rocky 9 desktop: xdg-open picks the session's file manager. Override with
# FLOW_FILE_MANAGER=nautilus (or dolphin, thunar, nemo) if that misfires.
FILE_MANAGER = os.environ.get("FLOW_FILE_MANAGER", "xdg-open")

# Two kill-switches for isolating a hard crash on a workstation. Both are on
# by default; setting either to 0 removes a whole class of Qt work from the
# grids without changing anything else, which is the fastest way to tell a
# thumbnail/pixmap fault apart from a compositing one.
#   FLOW_THUMBNAILS=0    no downloads, no QPixmap decoding, initials only
#   FLOW_TILE_SHADOWS=0  no QGraphicsDropShadowEffect on the tiles
THUMBNAILS_ENABLED = os.environ.get("FLOW_THUMBNAILS", "1") != "0"
TILE_SHADOWS_ENABLED = os.environ.get("FLOW_TILE_SHADOWS", "1") != "0"

# How long the opening sequence runs before it will hand over, even when the
# window was ready sooner. The stages inside it are fractions of this, so one
# number retimes the whole thing. FLOW_SPLASH_MS=0 skips it entirely, which
# is what you want when relaunching the app twenty times in a row.
SPLASH_MS = max(0, int(os.environ.get("FLOW_SPLASH_MS", "6000")))

SOFTWARE_FIELDS = [
    "code", "sg_status_list", "image",
    "linux_path", "linux_args", "version",
    "projects", "engine",
    "sg_external_ui", "sg_file_ext", "sg_rez_packages",
]

PROJECT_FIELDS = ["name", "sg_status", "image", "sg_description", "tank_name"]

# -- web Projects page (FLOW_PROXY) -------------------------------------------
#
# Columns the web Projects page asks for. A wish list, not a promise: it is
# intersected with the site's own Project schema first, so a field this site
# does not have drops out instead of failing the whole find(). Add custom
# fields here once FLOW_PROXY's /api/projects/meta shows they exist.
PROJECT_LIST_FIELDS = [
    "name", "tank_name", "sg_status", "sg_type", "sg_description",
    "sg_client", "sg_producer", "sg_project_manager", "sg_coordinator",
    "start_date", "end_date", "sg_start_date", "sg_due_date",
    "sg_delivery_date", "sg_priority", "image", "created_at", "updated_at",
]
# The only Project fields the web page may write. sg_status is deliberately
# absent: it decides which projects Flow lists, so one slip hides a show from
# every artist.
PROJECT_EDITABLE_FIELDS = ["sg_description"]
# Task fields the web Tasks page shows on top of TASK_FIELDS, if the site's
# Task schema has them. Same wish-list rule as PROJECT_LIST_FIELDS.
TASK_LIST_EXTRA_FIELDS = ["sg_priority_1", "sg_description", "updated_at"]
# The only Task fields the web page may write.
TASK_EDITABLE_FIELDS = ["sg_status_list", "start_date", "due_date",
                        TASK_OWNER_FIELD]
# Version fields the web review page reads, if the site has them. The mp4 and
# webm transcodes are what a browser can play; the original upload is often a
# .mov it cannot. All are presigned links that expire.
VERSION_WEB_MEDIA_FIELDS = [
    "sg_uploaded_movie_mp4", "sg_uploaded_movie_webm", "sg_uploaded_movie",
    "filmstrip_image", "sg_uploaded_movie_transcoding_status",
    "uploaded_movie_duration", "sg_uploaded_movie_frame_rate",
]
# The only Version field the web page may write: the review status.
VERSION_EDITABLE_FIELDS = ["sg_status_list"]

# Field on Software holding the rez request, e.g. "maya-2024 keentools-6.2".
SOFTWARE_REZ_FIELD = "sg_rez_packages"
# Package appended to every rez request so in-DCC tools can `import
# flow_context`. Set to "" to stop injecting it.
REZ_CONTEXT_PACKAGE = "flow_context"
# Package that carries the in-DCC tools and their startup hooks. Appended the
# same way, so launching a DCC is the whole install -- no artist ever copies a
# userSetup.py. It requires flow_context, so injecting it pulls in both.
# Set to "" to launch DCCs without the Flow menu.
REZ_DCC_PACKAGE = "flow_dcc"

# -- standalone publish ----------------------------------------------------

# Field on Version linking it back to the Task. Stock ShotGrid uses sg_task.
VERSION_TASK_FIELD = "sg_task"
# Field linking the Version to the Shot or Asset.
VERSION_ENTITY_FIELD = "entity"
# Field the media is uploaded to. This is what the SG player streams.
VERSION_MEDIA_FIELD = "sg_uploaded_movie"
# Status a freshly published Version gets. "" leaves it at the site default.
VERSION_STATUS = "rev"

# -- the version browser ---------------------------------------------------

# Fields read for the browser. Deep fields keep it to one query: the step comes
# off the linked task rather than a second round trip. A field the site does
# not have makes the whole find() fail, so anything non-stock added here has to
# exist -- VERSION_OPTIONAL_FIELDS below is the safe place for the rest.
# Only fields every ShotGrid site has. Anything a site might have renamed or
# removed belongs in VERSION_OPTIONAL_FIELDS below -- one unknown field fails
# the entire find(), which reads as "the version browser is broken".
VERSION_FIELDS = [
    "code", "description", "sg_status_list", "user", "created_by",
    "created_at", "updated_at", "entity", "sg_task", "image",
    "sg_task.Task.step",
    "sg_task.Task.content",
]

# Probed once against the schema and kept for the session if the site has them.
# These are stock fields, but stock is not the same as universal.
VERSION_OPTIONAL_FIELDS = [
    "sg_uploaded_movie", "sg_path_to_movie", "sg_path_to_frames",
    "sg_first_frame", "sg_last_frame", "frame_count", "frame_range",
]

# Versions fetched per page in the browser. The rest arrive on Load more.
VERSION_PAGE_SIZE = 100

# What "latest" means. ShotGrid's own timestamp, not the name: v010, v011, v002
# is a real publishing order and max(name) gets it wrong. Set to "code" only if
# a studio genuinely numbers versions in publish order.
LATEST_VERSION_FIELD = os.environ.get("FLOW_LATEST_VERSION_FIELD",
                                      "created_at")

# Fields read for the Latest Version column. Deliberately small: this is one
# query across every task on the page.
LATEST_VERSION_FIELDS = [
    "code", "created_at", "sg_status_list", "sg_task", "entity", "user",
]

# How long the browser waits after a keystroke before querying ShotGrid.
SEARCH_DEBOUNCE_MS = 300

# -- notes -----------------------------------------------------------------
#
# ShotGrid's Note/Reply model is the whole model: a Note links to entities
# through note_links and replies are Reply rows pointing back at it. Flow
# keeps no notes of its own.
NOTE_FIELDS = [
    "subject", "content", "user", "created_at", "updated_at",
    "note_links", "tasks", "sg_status_list", "attachments",
]
REPLY_FIELDS = ["content", "user", "created_at", "entity"]

# Read for note authors. permission_rule_set is ShotGrid's own idea of who
# somebody is (Artist, Manager, Client, ...) -- roles are not invented here.
USER_FIELDS = ["name", "email", "permission_rule_set", "image"]

# Notes are re-read on demand, never polled. This is the shortest gap between
# two refreshes that actually hits ShotGrid.
NOTES_MIN_REFRESH_SECONDS = 5

# -- production view -------------------------------------------------------
#
# Leads and reviewers: ShotGrid's own Task.task_reviewers, a multi-entity
# field of HumanUsers (and Groups). Their "To Review" list is the tasks that
# name them there.
TASK_LEAD_FIELD = os.environ.get("FLOW_TASK_LEAD_FIELD", "task_reviewers")
# Setting a task to this status in Flow asks the artist for a note and mails
# the task's leads (status_mail.py), through the pipeline's mailer.
REVIEW_MAIL_STATUS = os.environ.get("FLOW_REVIEW_MAIL_STATUS", "prw")
PIPELINE_ROOT = os.environ.get("PIPELINE_ROOT",
                               "/software/pipeline/vfx-ingest-pipeline")
#
# Anyone can switch the home page between their own tasks and every
# artist's. Logins listed in FLOW_PRODUCTION_USERS=rahul,priya open on
# every artist's.
PRODUCTION_USERS = [
    s.strip() for s in os.environ.get("FLOW_PRODUCTION_USERS", "").split(",")
    if s.strip()
]
# The ingestion / packaging dashboard. Production get a header button that
# opens it in the browser; the dashboard does its own login. Unset hides it.
DASHBOARD_URL = os.environ.get("FLOW_DASHBOARD_URL", "")
# Setting a task to one of these in Flow mails production (status_mail.py):
# the FLOW_PRODUCTION_MAIL addresses, or else the production logins above at
# the studio's mail domain.
PRODUCTION_MAIL_STATUSES = [
    s.strip() for s in os.environ.get(
        "FLOW_PRODUCTION_MAIL_STATUSES", "cmpt,pkg").split(",") if s.strip()
]
PRODUCTION_MAIL = [
    s.strip() for s in os.environ.get("FLOW_PRODUCTION_MAIL", "").split(",")
    if s.strip()
] or [f"{login}@{USER_EMAIL_DOMAIN}" for login in PRODUCTION_USERS]

# Live sync: every this many seconds Flow asks ShotGrid for task events newer
# than the last one it saw -- one small EventLogEntry query -- and reloads the
# home task list only when one touches it. 0 turns it off.
SYNC_SECONDS = int(os.environ.get("FLOW_SYNC_SECONDS", "60") or 0)

# -- the review inbox ------------------------------------------------------

# How far back "needs attention" looks. Everything older has been dealt with
# or has been overtaken by a newer version.
REVIEW_WINDOW_DAYS = int(os.environ.get("FLOW_REVIEW_DAYS", "30"))

# Version statuses that mean somebody is waiting on the artist. Short codes,
# because that is what the site stores -- check them against the site's own
# status list before trusting this map (see HANDOFF).
# Deliberately NOT "rev": that is the status a fresh publish gets
# (VERSION_STATUS above), so treating it as an action item would mark every
# version the artist just published as needing their attention.
REVIEW_STATUS_TYPES = {
    "rej": "VERSION_REJECTED",
    "rrq": "REVISION_REQUESTED",
    "vwd": "REVISION_REQUESTED",     # "viewed, changes wanted" on some sites
}

# Which items have been looked at. Ids and timestamps, nothing else: ShotGrid
# has no per-user read flag Flow is allowed to write.
REVIEW_READ_STATE_PATH = os.environ.get(
    "FLOW_REVIEW_STATE",
    os.path.expanduser("~/.flow/review_read.json"))

# Local copies of the movies uploaded to ShotGrid, so the review player plays
# from disk instead of streaming (media_cache.py). Per user and per machine:
# %LOCALAPPDATA%\Flow\media_cache on Windows, $XDG_CACHE_HOME/flow/media
# (~/.cache/flow/media) on Linux. Point FLOW_MEDIA_CACHE at a local disk
# where home directories live on NFS. The least recently played copies go
# first once the cache passes FLOW_MEDIA_CACHE_GB.
def _default_media_cache():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or             os.path.expanduser("~/AppData/Local")
        return os.path.join(base, "Flow", "media_cache")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "flow", "media")


MEDIA_CACHE_DIR = os.environ.get("FLOW_MEDIA_CACHE") or _default_media_cache()
MEDIA_CACHE_GB = float(os.environ.get("FLOW_MEDIA_CACHE_GB", "20"))

# Per-user UI choices: the app each task was last launched in, whether the
# home page's projects section is folded. See ui/ui_state.py.
UI_STATE_PATH = os.environ.get(
    "FLOW_UI_STATE", os.path.expanduser("~/.flow/ui_state.json"))

# Detail URL for an entity, used by "Open in ShotGrid".
def entity_url(entity_type, entity_id):
    return f"{SG_SITE.rstrip('/')}/detail/{entity_type}/{entity_id}"

# Extensions treated as movies rather than stills.
MOVIE_EXTENSIONS = {
    ".mov", ".mp4", ".mxf", ".avi", ".mkv", ".webm", ".m4v", ".r3d",
}
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".exr", ".dpx", ".tga", ".bmp",
}

# The one registry every extension check goes through. Add a format here and
# the file dialogs, the drop routing, the inspector and the publish validation
# all learn about it at once.
MEDIA_TYPES = {
    "movie": MOVIE_EXTENSIONS,
    "image": IMAGE_EXTENSIONS,
}


def media_kind(path):
    """"movie", "image", or "" for something we do not publish as media."""
    ext = os.path.splitext(path or "")[1].lower()
    for kind, extensions in MEDIA_TYPES.items():
        if ext in extensions:
            return kind
    return ""


def media_filter():
    """Qt file dialog filter string, built from the registry."""
    movies = " ".join(f"*{e}" for e in sorted(MOVIE_EXTENSIONS))
    images = " ".join(f"*{e}" for e in sorted(IMAGE_EXTENSIONS))
    return (f"Media ({movies} {images});;Movies ({movies});;"
            f"Images ({images});;All files (*)")


# Media inspection. ffprobe reads movies and stills alike, including EXR and
# DPX; without it the dialog falls back to Qt's image reader and file size.
FFPROBE = os.environ.get("FLOW_FFPROBE", "ffprobe")
FFPROBE_TIMEOUT = 20

# -- where media is allowed to come from -----------------------------------
#
# A Version whose media sits on somebody's desktop is a Version nobody else can
# use: not the farm, not review, not the artist who picks the shot up next
# week. The project root is derived from ENTITY_PATH_TEMPLATES above rather
# than written out again here -- one convention, one place.

# What happens to media outside the approved locations:
#   "strict"        - refused. Nothing outside the project publishes.
#   "approved_only" - refused unless it is under one of APPROVED_MEDIA_ROOTS.
#   "warn"          - allowed, with a warning the artist has to acknowledge.
# Left at "warn" because the farm's mounts have not been confirmed yet; move it
# to "approved_only" once they have. See HANDOFF §5e.
PATH_POLICY = os.environ.get("FLOW_PATH_POLICY", "warn")

# Locations outside the project that are still legitimate to publish from --
# shared plate stores, a vendor drop, a common elements library. {project} is
# the tank_name, {project_name} the display name. Set from the environment as
# a colon-separated list, or edit here.
APPROVED_MEDIA_ROOTS = [
    p for p in os.environ.get("FLOW_APPROVED_MEDIA_ROOTS", "").split(os.pathsep)
    if p.strip()
]

# The root every project lives under, used to tell "another show" apart from
# "not a show at all". Taken from the shared prefix of ENTITY_PATH_TEMPLATES.
JOBS_ROOT = os.environ.get("FLOW_JOBS_ROOT", "/jobs")

# Directories that are always the wrong place to publish from, matched on any
# path component. Local scratch that no render node can see.
UNSAFE_PATH_PARTS = [
    "desktop", "downloads", "documents",
    "tmp", "temp", "var/tmp", "appdata", "recycle.bin",
    "cache", "proxy_cache", ".cache", "trash",
]

# -- the optional work file ------------------------------------------------
#
# A publish may carry the DCC scene that produced the media (a .nk, .ma, .hip).
# It is always optional, and a failure to register it never fails the publish
# itself -- the Version and its media are already in ShotGrid by then.

# Scene files offered in the work file browser, mapped to a menu label.
WORKFILE_EXTENSIONS = {
    ".nk": "Nuke script",
    ".nknc": "Nuke script",
    ".ma": "Maya scene",
    ".mb": "Maya scene",
    ".hip": "Houdini scene",
    ".hipnc": "Houdini scene",
    ".hiplc": "Houdini scene",
    ".blend": "Blender scene",
    ".c4d": "Cinema 4D scene",
    ".3de": "3DEqualizer scene",
    ".mocha": "Mocha project",
    ".sfx": "Silhouette project",
    ".spp": "Substance Painter project",
    ".sbs": "Substance Designer graph",
    ".psd": "Photoshop document",
    ".aep": "After Effects project",
    ".katana": "Katana scene",
    ".usd": "USD scene",
    ".usda": "USD scene",
    ".usdc": "USD scene",
    ".zpr": "ZBrush project",
    ".ztl": "ZBrush tool",
}

# How the work file is registered in ShotGrid:
#   "published_file" - create a PublishedFile pointing at the file where it
#                      already lives on /jobs. Nothing is uploaded, which is
#                      the point: scene files are large and already on shared
#                      storage. Needs create permission on PublishedFile.
#   "attachment"     - upload the file itself onto the Version. Simplest
#                      permission-wise, but it copies the bytes into ShotGrid.
#   "path_only"      - write the path onto the Version and nothing else.
WORKFILE_MODE = os.environ.get("FLOW_WORKFILE_MODE", "published_file")

# Fields used when WORKFILE_MODE is "published_file". All stock names.
PUBLISHED_FILE_TASK_FIELD = "task"
PUBLISHED_FILE_ENTITY_FIELD = "entity"
PUBLISHED_FILE_VERSION_FIELD = "version"
PUBLISHED_FILE_PATH_FIELD = "path"

# PublishedFileType looked up per extension. A type that does not exist on the
# site is skipped rather than created, so this list can be optimistic.
PUBLISHED_FILE_TYPES = {
    ".nk": "Nuke Script",
    ".nknc": "Nuke Script",
    ".ma": "Maya Scene",
    ".mb": "Maya Scene",
    ".hip": "Houdini Scene",
    ".hipnc": "Houdini Scene",
    ".blend": "Blender Scene",
    ".psd": "Photoshop Document",
    ".aep": "After Effects Project",
    ".usd": "USD Scene",
    ".usda": "USD Scene",
}

# Text field on Version holding the work file path, so the path is visible
# without opening the PublishedFile. Leave "" to append it to the description
# instead, which is the only text field every site is guaranteed to have.
VERSION_WORKFILE_FIELD = os.environ.get("FLOW_VERSION_WORKFILE_FIELD", "")

# rez entry point. Set FLOW_REZ_EXECUTABLE to an absolute path when the
# artist's login shell does not put rez on PATH.
REZ_EXECUTABLE = os.environ.get("FLOW_REZ_EXECUTABLE", "rez")


def _merge_login_shell_env(env_dump):
    """Fold PATH and REZ_* from `env -0` output into os.environ.

    PATH entries are appended, so the session's own order still wins; REZ_*
    only fills what is unset. Anything ~/.bashrc prints lands in front of the
    first entry and is skipped with it -- PATH is never the first one.
    """
    have = os.environ.get("PATH", "").split(os.pathsep)
    for item in env_dump.split(b"\0"):
        key, _, value = item.decode(errors="replace").partition("=")
        if key == "PATH":
            have += [p for p in value.split(os.pathsep) if p and p not in have]
            os.environ["PATH"] = os.pathsep.join(p for p in have if p)
        elif key.startswith("REZ_"):
            os.environ.setdefault(key, value)


def _borrow_rez_from_login_shell():
    """Recover the studio rez setup when Flow starts without it.

    A desktop icon starts Flow from the graphical session, which never reads
    ~/.bashrc, so the rez setup sourced there is missing -- for the frozen
    binary and flow.sh alike. Asking an interactive login shell for its
    environment picks up the same PATH (and REZ_CONFIG_FILE) a terminal has.
    """
    if not sys.platform.startswith("linux") or shutil.which(REZ_EXECUTABLE):
        return
    try:
        dump = subprocess.run(
            ["bash", "-lic", "env -0"], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return
    _merge_login_shell_env(dump)


_borrow_rez_from_login_shell()

# Released DCC packages, one folder per package and per version inside it.
# The right-click menu on a task is built from this tree.
DCC_PACKAGES_ROOT = os.environ.get(
    "FLOW_DCC_ROOT", "/software/packages/dcc")

# Fallback for checking whether a package exists, used only when
# REZ_PACKAGES_PATH is not already in the environment. Mirrors the search
# path in /software/pipeline/init_source/configs/rezconfig.py.
REZ_PACKAGE_PATHS = [
    "/software/packages/plugins",
    "/software/packages/tools",
    "/software/packages/dcc",
    "/software/packages/libs",
    "/software/packages/external",
    "/software/packages/dev",
    os.path.expanduser("~/packages"),
]

# Executable to run inside `rez env <package>`, when it is not just the package
# name. Add an entry here whenever a package's alias differs from its name.
DCC_COMMANDS = {
    "3de": "DD3DE4",
    "mochapro": "mocha",
    "openrv": "rv",
    "silhouette": "sfx",
}

# Pretty names for the menu. Anything absent is title-cased.
DCC_LABELS = {
    "3de": "3DEqualizer",
    "openrv": "OpenRV",
    "mochapro": "Mocha Pro",
    "pureref": "PureRef",
    "meshlab": "MeshLab",
}

# Per-project env YAMLs live here; falls back to default.yml
ENVS_DIR = os.path.join(os.path.dirname(__file__), "envs")

# Source tree of the in-DCC tools, laid out the same way the rez package
# installs them (flow_dcc/ importable, startup/<dcc>/ per host). Launches
# point PYTHONPATH here so the menu appears even when the rez package is not
# released or the Software entity requests no rez packages at all. A resolved
# flow_dcc prepends its own copy, so it still wins when present.
DCC_SOURCE_ROOT = os.environ.get(
    "FLOW_DCC_SOURCE",
    os.path.join(os.path.dirname(__file__), "rez", "flow_dcc"))
CONTEXT_SOURCE_ROOT = os.environ.get(
    "FLOW_CONTEXT_SOURCE",
    os.path.join(os.path.dirname(__file__), "rez", "flow_context"))

# --- OpenRV review player ---------------------------------------------------
# Review runs through RV rather than Flow's own QMediaPlayer: EXR sequences,
# colour management and A/B compare are all things RV has and Qt does not.
#
# RV is launched through rez (`rez env openrv -- rv`) so it arrives with its own
# environment. Set FLOW_RV to an absolute path only to bypass rez entirely --
# point it at the `rv` wrapper, not rv.bin, or RV starts without RV_HOME.
RV_EXECUTABLE = os.environ.get("FLOW_RV", "")
RV_PACKAGE = os.environ.get("FLOW_RV_PACKAGE", "openrv")
# Pin a version here; empty means the newest one released in the rez tree.
RV_VERSION = os.environ.get("FLOW_RV_VERSION", "")
# Last-resort search when rez is not on PATH for this session. Matches the farm
# layout: openrv/<version>/platform-linux/os-rocky-9.6/bin/rv
# Joined with "/" rather than os.path.join: DCC_PACKAGES_ROOT is a POSIX mount
# on the farm, and a Windows dev box would otherwise build a pattern with
# separators glob can never match.
RV_GLOB = os.environ.get(
    "FLOW_RV_GLOB",
    "/".join([DCC_PACKAGES_ROOT.rstrip("/"), RV_PACKAGE, "*", "platform-*",
              "os-*", "bin", "rv"]))
# Flags every review gets. Empty by default -- the artist's own RV preferences
# should win. e.g. FLOW_RV_ARGS="-fullscreen -nofloat"
RV_ARGS = shlex.split(os.environ.get("FLOW_RV_ARGS", ""))

# --- client delivery (package_service) ---------------------------------------
# Version statuses that mean "the client may have this". The newest Version per
# shot in one of these is what gets delivered.
DELIVERY_APPROVED_STATUSES = [
    s for s in os.environ.get("FLOW_DELIVERY_APPROVED", "apr").split(",")
    if s.strip()
]
# Same outbox the event daemon's sg_shot_delivery plugin writes to, so a shot
# delivered from Flow and one delivered by status change land side by side.
DELIVERY_OUTBOX = os.environ.get("FLOW_DELIVERY_OUTBOX",
                                 "/jobs/DataIO/Outbox/client")
# Shot status written after a clean delivery. Deliberately not "dlvr": that
# status triggers the daemon plugin, which would deliver the shot a second time.
# "" leaves the status alone.
DELIVERY_DONE_STATUS = os.environ.get("FLOW_DELIVERY_DONE_STATUS", "dlvd")

APP_TITLE = "Flow"
