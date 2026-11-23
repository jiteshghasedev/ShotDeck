"""Silhouette startup hook: builds the 5and8 menu.

Silhouette imports every folder listed in SFX_SCRIPT_IMPORTS that holds an
__init__.py. Flow's launcher (and the flow_dcc rez package) put this folder
there. Failure is printed, never raised -- a roto artist should get
Silhouette without the menu rather than no Silhouette at all.
"""

import traceback

try:
    import flow_dcc
    flow_dcc.install()
except Exception:
    traceback.print_exc()
