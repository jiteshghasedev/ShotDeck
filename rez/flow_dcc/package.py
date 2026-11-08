name = "flow_dcc"

version = "1.2.0"

description = \
    "Flow's in-DCC tools: the Python package the adapters live in, plus " \
    "the per-DCC startup hooks that load them automatically. Resolving this " \
    "package is the whole install -- artists never copy a userSetup.py or " \
    "edit a personal PYTHONPATH."

authors = ["pipeline"]

# flow_context reads the launch context; this package is the tooling on top
# of it. Requiring it means one request (flow_dcc) pulls in both.
requires = [
    "flow_context",
]

variants = [
    ["platform-linux", "arch-x86_64"],
]

build_command = "python {root}/REZ_INSTALLER.py"


def commands():
    # `import flow_dcc` inside any DCC.
    env.PYTHONPATH.prepend("{root}/python")

    # Where the adapters look for their own startup trees, and what the probe
    # scripts report on.
    env.FLOW_DCC_ROOT = "{root}"

    # The startup hooks are wired per DCC, and only for the DCC that is
    # actually being launched -- Maya's userSetup.py must never end up on
    # Nuke's PYTHONPATH, and NUKE_PATH means nothing to Maya.
    #
    # Two signals, because either one alone has a hole: the resolve misses a
    # studio that wraps the DCC under another package name, and the context
    # variable is absent when someone runs `rez env maya flow_dcc` by
    # hand outside Flow. Matching either is what makes the menu appear
    # without anyone typing a Python command.
    software = str(env.FLOW_SOFTWARE).lower() \
        if "FLOW_SOFTWARE" in env else ""

    if "maya" in resolve or software.startswith("maya"):
        # Maya executes any userSetup.py it finds on PYTHONPATH at startup.
        env.PYTHONPATH.prepend("{root}/startup/maya")

    if "nuke" in resolve or software.startswith("nuke"):
        # Nuke scans every NUKE_PATH entry for init.py (always) and menu.py
        # (GUI sessions only).
        env.NUKE_PATH.prepend("{root}/startup/nuke")

    if "houdini" in resolve or software.startswith("houdini"):
        # Houdini runs scripts/123.py (empty session) or scripts/456.py (one
        # that opens a scene) from every HOUDINI_PATH entry.
        env.HOUDINI_PATH.prepend("{root}/startup/houdini")
        # "&" is Houdini's own default path list. A HOUDINI_PATH that does not
        # contain it is a Houdini with none of its own tools, so it goes back
        # on the end whether or not something else already added it.
        env.HOUDINI_PATH.append("&")

    if "blender" in resolve or software.startswith("blender"):
        # Blender imports <scripts>/startup/*.py once its UI exists. This does
        # shadow the artist's personal scripts folder for the session, which is
        # the price of a menu that appears without anyone installing an add-on.
        env.BLENDER_USER_SCRIPTS = "{root}/startup/blender"

    if "substance" in resolve or software.startswith("substance"):
        # Painter loads every plugin on this path and calls its start_plugin().
        env.SUBSTANCE_PAINTER_PLUGINS_PATH.prepend("{root}/startup/substance")

    if "3de" in resolve or software.startswith("3de"):
        # 3DE has no runtime menu API: it builds its menus at startup from the
        # header comments of the scripts it finds here.
        env["3DE4_PYTHON_CUSTOM_SCRIPTS_DIR"].prepend("{root}/startup/3de")

    # Rhino is deliberately not wired: its menus come from a per-user .rui
    # workspace that Python cannot add to. The adapter works and the actions
    # run from the Python editor -- see startup/rhino/README.txt.
    #
    # After Effects, Photoshop and ZBrush have no in-process Python at all, so
    # there is nothing to load into them. Their folder layouts still live in
    # flow_dcc/paths.py, and publishing them stays Flow's own job.

    # Silhouette is deliberately not wired here yet: its startup mechanism has
    # not been confirmed on the 5and8 farm, and guessing an environment
    # variable would fail silently rather than loudly. Run
    #   rez env silhouette flow_dcc -- python {root}/probe_silhouette.py
    # inside Silhouette's interpreter and wire the result in. The adapter and
    # the menu code are already written and work once something calls
    # flow_dcc.install().
