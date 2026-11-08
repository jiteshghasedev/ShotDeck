"""Flow's tools, running inside the DCC.

Resolving the flow_dcc rez package is the entire install: it puts this
package on PYTHONPATH and wires the host's own startup mechanism, which calls

    import flow_dcc
    flow_dcc.install()

Artists never copy a userSetup.py, never edit PYTHONPATH, and never type a
setup command.

Which adapter loads is decided by FLOW_SOFTWARE, normalised by Flow at
launch. Host modules (maya.cmds, nuke, fx) are imported inside the adapter that
owns them, never at module scope, so importing this package inside Nuke cannot
drag Maya code in with it.
"""

import logging
import os

from . import context, deadline, paths, publish, versioning

__version__ = "1.5.0"

__all__ = ["context", "deadline", "paths", "publish", "versioning",
           "install", "adapter", "log"]

# One named logger. DCCs route logging to their own script editor or terminal,
# so this shows up where the artist already looks.
log = logging.getLogger("flow")
if not log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("Flow: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

# Which adapter each normalised name maps to. The normalising itself lives in
# paths.ALIASES, because the folder layout has to agree with the adapter about
# what "3de" means.
#
# After Effects, Photoshop and ZBrush are deliberately absent: none of them
# host an in-process Python interpreter, so there is nothing for an adapter to
# run inside. Their folders are still in paths.LAYOUT, and publishing them is
# Flow's own publish dialog's job.
ADAPTERS = ("maya", "nuke", "silhouette", "houdini", "blender", "tde4",
            "substance", "rhino")


def software(ctx=None):
    """Normalised adapter name for this session, e.g. "maya". "" when unknown."""
    ctx = ctx or context.get()
    return paths.normalise(ctx.software)


def adapter(name=None):
    """Import and return the adapter module for this DCC, or None.

    Imported by name rather than by trying each in turn: loading the Maya
    adapter inside Nuke is exactly what this indirection exists to prevent.
    """
    name = name or software()
    if name not in ADAPTERS:
        return None

    import importlib
    return importlib.import_module("{0}.adapters.{1}".format(__name__, name))


def install(name=None):
    """Load this DCC's adapter and build the Flow menu.

    Called from the startup hooks, so it must never raise: a broken menu is
    worth a line in the script editor, not a DCC that will not open a scene.
    Returns True when the menu was built.
    """
    ctx = context.get()
    missing = ctx.missing()
    if missing:
        log.warning("launched outside Flow (no %s) — menu not built",
                    ", ".join(missing))
        return False

    name = name or software(ctx)
    module = adapter(name)
    if module is None:
        log.warning("no adapter for FLOW_SOFTWARE=%r — menu not built",
                    ctx.software)
        return False

    try:
        built = module.install()
    except Exception:
        log.exception("could not build the Flow menu in %s", name)
        return False

    if not built:
        # The adapter already said why (batch session, unconfirmed menu API).
        # Not raising is not the same as having built a menu.
        return False

    log.info("%s tools loaded for %s / %s",
             name, ctx.project_name, ctx.task_name or "no task")
    return True


def root():
    """Install root of the resolved rez package, or "" outside one."""
    return os.environ.get("FLOW_DCC_ROOT", "")
