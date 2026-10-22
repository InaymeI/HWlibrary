#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    python -m venv .venv
    ./.venv/bin/pip install -U pip
    ./.venv/bin/pip install PyQt6 requests
fi
if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
    export QT_WAYLAND_DISABLE_WINDOWDECORATION=1
fi
exec ./.venv/bin/python -m mt "$@"
