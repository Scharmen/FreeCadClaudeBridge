#!/bin/sh
# FreeCAD x Claude: one-step installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/Scharmen/FreeCadClaudeBridge/main/install.sh | sh
#
# Needs FreeCAD and Claude Desktop installed and signed in; the wizard checks both and shows
# where to get what is missing. It installs uv (if missing, after asking), the Claude plugin
# and the Claude panel for FreeCAD. It opens a setup window; options are passed on:
#   ... | sh -s -- --yes      (no questions, in the terminal)      ... | sh -s -- --no-gui
# FREECAD_BRIDGE_SRC overrides where the installer itself comes from (CI uses the checkout).
set -e

SRC="${FREECAD_BRIDGE_SRC:-freecad-bridge @ https://github.com/Scharmen/FreeCadClaudeBridge/archive/refs/heads/main.zip#subdirectory=plugin/server}"

echo "FreeCAD x Claude installer"
echo

find_uvx() {
    for c in "$(command -v uvx 2>/dev/null || true)" "$HOME/.local/bin/uvx" "$HOME/.cargo/bin/uvx"; do
        if [ -n "$c" ] && [ -x "$c" ]; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

# Answers come from the terminal even when this script itself arrives through a pipe.
if [ -r /dev/tty ] && [ -t 1 ]; then
    TTY=/dev/tty
else
    TTY=/dev/null
fi

UVX="$(find_uvx || true)"
if [ -z "$UVX" ]; then
    echo "The installer needs uv, a small tool from Astral that runs the FreeCAD bridge"
    echo "(https://docs.astral.sh/uv/)."
    answer=y
    if [ "$TTY" = /dev/tty ]; then
        printf "  Install uv now? [Y/n] "
        read -r answer < /dev/tty || answer=y
    fi
    case "$answer" in
        [nN]*) echo "Nothing was changed. Install uv and run this installer again."; exit 1 ;;
    esac
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UVX="$(find_uvx || true)"
    if [ -z "$UVX" ]; then
        echo "uv was installed but not found; open a new terminal and run this installer again."
        exit 1
    fi
fi

# A window for people who started it themselves; the terminal for CI, --yes and --no-gui.
GUI=--gui
if [ "$TTY" = /dev/null ] || [ -n "$CI" ]; then
    GUI=
fi
for a in "$@"; do
    case "$a" in
        --yes|--no-gui) GUI= ;;
    esac
done
if [ -n "$GUI" ]; then
    echo "Opening the setup window …"
fi

# --refresh: always the current installer, not a cached copy from an earlier run.
# Managed Python first: it brings Tk for the window (some system Pythons have none).
exec "$UVX" --refresh --python-preference managed --from "$SRC" freecad-bridge wizard $GUI "$@" < "$TTY"
