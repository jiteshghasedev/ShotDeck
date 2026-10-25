"""Local test environment: Flow signed in as jitesh@5and8.ai, real ShotGrid.

    py test_env/flow_test.py seed     # build test_env/jobs from jitesh's SG tasks
    py test_env/flow_test.py run      # start Flow as jitesh against that tree

The AD bind is skipped with SGDESK_DEV_USER (auth/ad_auth.py), since a dev
box is not on 5and8.net; ShotGrid is the live site through the script key in
SG_SCRIPT_KEY. The /jobs templates are re-rooted under test_env/jobs, and
Flow's per-user state goes to test_env/_state instead of ~/.flow.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JOBS = os.path.join(HERE, "jobs").replace("\\", "/")
STATE = os.path.join(HERE, "_state")

LOGIN = "jitesh"
EMAIL = "jitesh@5and8.ai"

os.environ["SGDESK_DEV_USER"] = LOGIN
os.environ["SGDESK_USER_EMAIL"] = EMAIL
os.environ.pop("SGDESK_DEV", None)          # "1" swaps in the mock ShotGrid
os.environ["FLOW_JOBS_ROOT"] = JOBS
os.environ.setdefault("FLOW_LOG_DIR", os.path.join(STATE, "logs"))
os.environ.setdefault("FLOW_UI_STATE", os.path.join(STATE, "ui_state.json"))
os.environ.setdefault("FLOW_REVIEW_STATE", os.path.join(STATE, "review_read.json"))

sys.path.insert(0, ROOT)

import config  # noqa: E402

config.ENTITY_PATH_TEMPLATES = {
    kind: template.replace("/jobs", JOBS, 1)
    for kind, template in config.ENTITY_PATH_TEMPLATES.items()
}


def seed():
    """Make every folder Flow's "Open folder" menu lists for jitesh's tasks."""
    import paths
    from sg_client import SGClient

    sg = SGClient()
    if not sg.resolve_owner(EMAIL):
        sys.exit(f"no {config.TASK_OWNER_ENTITY} with email {EMAIL} in ShotGrid")
    tasks = sg.all_my_tasks()
    made = set()
    for task in tasks:
        folders = paths.folders(task["project"], task)
        if not folders:
            print(f"skipped {task['content']}: no path (missing sequence?)")
        for _label, path, _exists in folders:
            os.makedirs(path, exist_ok=True)
            made.add(path)
    print(f"{len(tasks)} tasks, {len(made)} folders under {JOBS}")


def run():
    import main
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    return main.main()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "seed":
        seed()
    elif command == "run":
        sys.exit(run())
    else:
        sys.exit(__doc__)
