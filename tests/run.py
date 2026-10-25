"""Run Flow's tests.

    python tests/run.py            # everything
    python tests/run.py publish    # only modules whose name contains "publish"

No pytest: the farm's venv has the ShotGrid API and PySide6 and nothing else,
and a test suite nobody can run is not a test suite. UI tests force Qt's
offscreen platform, so this works over ssh.
"""

import importlib
import os
import sys
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SG_SCRIPT_KEY", "test-key-not-real")
os.environ.setdefault("FLOW_LOG_DIR",
                      os.path.join(HERE, "_logs"))
# Never read or write the developer's own ~/.flow state from a test run.
os.environ.setdefault("FLOW_UI_STATE",
                      os.path.join(HERE, "_logs", "ui_state.json"))

# shotgun_api3 is not installed on a developer's Windows box, and none of these
# tests reach the network. The client is imported for real either way.
if "shotgun_api3" not in sys.modules:
    try:
        import shotgun_api3            # noqa: F401
    except ImportError:
        sys.modules["shotgun_api3"] = types.ModuleType("shotgun_api3")

MODULES = [
    "test_media_inspector",
    "test_path_validator",
    "test_version_query",
    "test_preflight",
    "test_publish_service",
    "test_package_service",
    "test_notes_service",
    "test_latest_version",
    "test_review_service",
    "test_sg_client",
    "test_projects",
    "test_web_tasks",
    "test_auth",
    "test_dcc",
    "test_usd",
    "test_launch_env",
    "test_plates",
    "test_rv_review", "test_status_mail",
    "test_ui",
]


def main(argv):
    wanted = argv[1:]
    modules = [m for m in MODULES
               if not wanted or any(w in m for w in wanted)]

    passed = failed = 0
    failures = []
    for name in modules:
        module = importlib.import_module(name)
        setup = getattr(module, "setup_module", None)
        if setup:
            setup()
        for attr in sorted(dir(module)):
            if not attr.startswith("test_"):
                continue
            test = getattr(module, attr)
            try:
                test()
            except Exception:
                failed += 1
                failures.append((f"{name}.{attr}", traceback.format_exc()))
                print(f"FAIL  {name}.{attr}")
            else:
                passed += 1
                print(f"ok    {name}.{attr}")

    print()
    for name, tb in failures:
        print(f"--- {name}")
        print(tb)
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
