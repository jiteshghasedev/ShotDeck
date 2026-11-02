"""Host-specific code. One module per DCC, imported only inside that DCC.

Every adapter offers the same three things to the shared code above it:

    install()            build the Flow menu
    save_scene(path)     save the open scene to path
    message(text)        put text where this host shows messages

Two more are optional, and only matter to Deadline: frame_range() and
deadline_plugin_info(scene). A host that renders on the farm implements them;
one that does not is refused by deadline.PLUGINS long before they are called.

The menu itself is described once, in ACTIONS, so the hosts differ only in how
they build menus rather than in what is on them.
"""

# (label, callable name on the adapter module). None is a separator.
#
# Grouped by what the artist is doing: working on the scene, handing it off,
# then finding things. The two that leave the workstation -- Publish and
# Submit -- sit in a band of their own between the two, so neither is hit by
# accident on the way to Save.
ACTIONS = [
    ("Load File...", "action_load_file"),
    ("Save", "action_save"),
    ("Save As...", "action_save_as"),
    ("Version Up", "action_version_up"),
    (None, None),
    ("Publish...", "action_publish"),
    ("Submit to Deadline...", "action_submit"),
    (None, None),
    ("Open Work Folder", "action_open_work_folder"),
    ("Open Publish Folder", "action_open_publish_folder"),
    ("Context", "action_context"),
]

# USD assembly and publish. Added to the menu of any host that can write a USD
# layer, i.e. whose adapter defines export_usd(path, root_prim) and
# load_usd_stage(path). See flow_dcc/usd.py.
USD_ACTIONS = [
    (None, None),
    ("Load USD Stage", "action_load_usd_stage"),
    ("Publish USD Layer...", "action_publish_usd"),
    ("Open USD Folder", "action_open_usd_folder"),
]


def actions(module):
    """The menu for one host: the shared list, plus USD where it applies."""
    return ACTIONS + (USD_ACTIONS if hasattr(module, "export_usd") else [])


MENU_NAME = "5and8"
