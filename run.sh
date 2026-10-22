#!/usr/bin/env bash
# Launcher: creates a virtualenv on first run, then starts the app.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [ ! -d .venv ]; then
    echo "→ создаю виртуальное окружение (.venv)…"
    "$PYTHON" -m venv .venv
    ./.venv/bin/pip install -q -U pip
    ./.venv/bin/pip install -q -e .
fi

# Keep the venv in sync when pyproject.toml changes.
if [ pyproject.toml -nt .venv/.installed ]; then
    echo "→ обновляю зависимости…"
    ./.venv/bin/pip install -q -e .
    touch .venv/.installed
fi

# Wayland-first on Hyprland / Wayland, X11 fallback otherwise.
if [ -z "${QT_QPA_PLATFORM:-}" ]; then
    if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
        export QT_QPA_PLATFORM="wayland;xcb"
        export QT_WAYLAND_DISABLE_WINDOWDECORATION=1
    fi
fi

exec ./.venv/bin/python -m mt "$@"
