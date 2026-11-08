import argparse
import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

import applog
import auth
import config
from auth import login_dialog
from sg_client import SGClient
from ui.branding import Splash
from ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description="Flow")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every command and environment detail")
    parser.add_argument("--console", action="store_true",
                        help="open with the Terminal panel already showing")
    args = parser.parse_args()

    log = applog.setup(verbose=args.verbose)
    log.info("Flow starting")

    app = QApplication(sys.argv)

    # Up before anything else, so launching Flow shows Flow rather
    # than a bare desktop while AD and ShotGrid are contacted.
    splash = Splash("Loading Pipeline")
    splash.show()
    app.processEvents()

    def prompt(*args, **kwargs):
        """Get the splash out of the way of the login dialog, then bring it
        back. On a domain-joined box this never runs and the splash is up for
        the whole of startup."""
        splash.hide()
        try:
            return login_dialog.prompt(*args, **kwargs)
        finally:
            splash.show()          # restarts the sequence from the top

    # Authenticate before anything talks to ShotGrid or launches a DCC. On a
    # domain-joined box this is silent; otherwise it prompts. Fail closed.
    result = auth.authenticate(prompt=prompt)
    if not result.authorized:
        log.error("authentication failed: %s", result.reason)
        splash.close()
        QMessageBox.critical(None, "Flow",
                             result.reason or "You are not authorized to use "
                                              "Flow.")
        return 1
    log.info("signed in as %s (%s)", result.login, result.method)
    splash.set_message("Connecting to ShotGrid")

    try:
        sg = SGClient()
    except Exception as e:
        # Most often a missing SG_SCRIPT_KEY; a dialog beats a traceback into
        # a terminal the artist never sees.
        log.error("could not connect to ShotGrid: %s", e)
        splash.close()
        QMessageBox.critical(None, "Flow", str(e))
        return 1
    # RV and the DCCs read the script user from the environment, not from
    # config, so a key that came from config's default must be exported too.
    os.environ.setdefault("SG_SCRIPT_NAME", config.SG_SCRIPT_NAME)
    os.environ.setdefault("SG_SCRIPT_KEY", config.SG_SCRIPT_KEY)

    splash.set_message("Preparing Workspace")
    win = MainWindow(sg, login=result.login, auth_result=result)
    if args.console:
        win.show_console()
    # The window is deliberately not shown here: finish() shows it behind the
    # fade, so the projects appear as the splash goes rather than under it.
    splash.finish(win)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
