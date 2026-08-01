#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
RUNTIME_ROOT=${CODEX_ROUTER_ROOT:-"$HOME/.codex/auto-router"}
APP_PARENT=${CODEX_ROUTER_APP_DIR:-"$HOME/Applications"}
PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}

mkdir -p "$RUNTIME_ROOT" "$APP_PARENT"
cp "$PROJECT_DIR/codex_router.py" "$RUNTIME_ROOT/codex_router.py"
if [ ! -f "$RUNTIME_ROOT/router_config.json" ]; then
  cp "$PROJECT_DIR/router_config.json" "$RUNTIME_ROOT/router_config.json"
else
  echo "Keeping existing configuration: $RUNTIME_ROOT/router_config.json"
fi

"$PYTHON_BIN" "$PROJECT_DIR/scripts/manage_hooks.py" install

if command -v xcrun >/dev/null 2>&1; then
  "$PROJECT_DIR/macos-widget/build-widget.sh" >/dev/null
  ditto "$PROJECT_DIR/build/Codex Auto Router.app" "$APP_PARENT/Codex Auto Router.app"
  echo "Installed menu bar app: $APP_PARENT/Codex Auto Router.app"
else
  echo "xcrun was not found; skipping the optional menu bar app."
fi

CODEX_ROUTER_ROOT="$RUNTIME_ROOT" "$PYTHON_BIN" "$RUNTIME_ROOT/codex_router.py" stop >/dev/null
CODEX_ROUTER_ROOT="$RUNTIME_ROOT" "$PYTHON_BIN" "$RUNTIME_ROOT/codex_router.py" watch --all --daemon

echo
echo "Installation complete."
echo "One-time step: open Codex CLI, run /hooks, and trust the UserPromptSubmit hook."
echo "Configuration: $RUNTIME_ROOT/router_config.json"
