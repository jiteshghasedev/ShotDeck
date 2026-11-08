#!/usr/bin/env bash
# Build the standalone Flow binary. Run this ON ROCKY 9 -- PyInstaller does
# not cross-compile, so a Windows build produces a Windows exe and nothing else.
#
#   cd /software/pipeline/Flow && ./tools/build/build_rocky9.sh
#   FLOW_BUILD_PYTHON=/opt/python3.11/bin/python3 ./tools/build/build_rocky9.sh
#
# Output:
#   dist/Flow/Flow          the binary
#   dist/Flow-<ver>-el9.tar.gz  the same folder, ready to copy to a farm box
#
# The build venv lives in build/venv and is reused; delete it to start clean.
# Qt needs these on the build box and on every box that runs the result:
#   sudo dnf install -y libxkbcommon-x11 xcb-util-cursor xcb-util-wm \
#        xcb-util-keysyms xcb-util-image xcb-util-renderutil \
#        libxkbcommon fontconfig mesa-libGL
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
cd "$APP_ROOT"

[ "$(uname -s)" = "Linux" ] || {
    echo "build: this produces an el9 binary and must run on Rocky 9, not $(uname -s)." >&2
    exit 1
}
[ -f main.py ] || { echo "build: no application at $APP_ROOT/main.py" >&2; exit 1; }

# 3.11 first: PySide6 wheels for it are current, and Rocky 9's stock 3.9 is not.
PYTHON="${FLOW_BUILD_PYTHON:-$(command -v python3.11 || command -v python3.12 || command -v python3)}"
[ -n "$PYTHON" ] && [ -x "$PYTHON" ] || {
    echo "build: no Python found -- dnf install python3.11, or set FLOW_BUILD_PYTHON." >&2
    exit 1
}
# An activated venv's python3.11 wins the PATH lookup above. Build from the
# interpreter it was made from, not from another venv's symlink.
PYTHON="$("$PYTHON" -c 'import sys; print(getattr(sys, "_base_executable", "") or sys.executable)')"
echo "python : $PYTHON ($("$PYTHON" -V))"

# PyInstaller imports ctypes. A Python compiled from source without
# libffi-devel has no _ctypes, and fails only after the whole pip install.
"$PYTHON" -c "import ctypes" 2>/dev/null || {
    echo "build: $PYTHON has no ctypes (built without libffi-devel)." >&2
    echo "       Use Rocky's own: sudo dnf install -y python3.11 &&" >&2
    echo "       FLOW_BUILD_PYTHON=/usr/bin/python3.11 $0" >&2
    exit 1
}

VENV="$APP_ROOT/build/venv"
# Rebuild the venv when it is broken (a failed run leaves bin/python as a dead
# link) or was made from a different interpreter than this run uses.
# pyvenv.cfg's "home" is the folder of the interpreter that made it.
VENV_HOME="$(sed -n 's/^home *= *//p' "$VENV/pyvenv.cfg" 2>/dev/null || true)"
if ! "$VENV/bin/python" -V >/dev/null 2>&1 || \
        [ "$VENV_HOME" != "$(dirname "$PYTHON")" ]; then
    rm -rf "$VENV"
    echo "creating build venv at $VENV"
    # Shared mounts under /software may refuse symlinks, which venv uses by
    # default; copying the interpreter works everywhere.
    "$PYTHON" -m venv "$VENV" 2>/dev/null && "$VENV/bin/python" -V >/dev/null 2>&1 || {
        rm -rf "$VENV"
        echo "symlinked venv failed here -- retrying with copies"
        "$PYTHON" -m venv --copies "$VENV"
    }
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r requirements.txt pyinstaller

# --clean, so a stale Analysis cache never silently ships an old module.
"$VENV/bin/python" -m PyInstaller --clean --noconfirm \
    --distpath dist --workpath build/pyinstaller \
    tools/build/flow.spec

BIN="$APP_ROOT/dist/Flow/Flow"
[ -x "$BIN" ] || { echo "build: PyInstaller reported success but $BIN is missing." >&2; exit 1; }

# Smoke test: argparse exits before QApplication, so this proves the bundle
# imports and starts without needing a display on the build box.
"$BIN" --help >/dev/null || { echo "build: $BIN cannot start -- see the traceback above." >&2; exit 1; }

VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' config.py)"
[ -n "$VERSION" ] || { echo "build: no APP_VERSION in config.py" >&2; exit 1; }
TARBALL="$APP_ROOT/dist/Flow-${VERSION}-el9.tar.gz"
tar -czf "$TARBALL" -C "$APP_ROOT/dist" Flow

echo
echo "binary  : $BIN"
echo "tarball : $TARBALL"
echo
echo "Deploy: untar under /software/pipeline, then run Flow/Flow."
echo "Credentials still come from /etc/sgdesk/sgdesk.env (DEPLOY-ROCKY9 §5)."
