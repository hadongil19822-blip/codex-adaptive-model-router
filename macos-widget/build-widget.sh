#!/bin/sh
set -eu

WIDGET_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PROJECT_DIR=$(dirname "$WIDGET_DIR")
APP_DIR="$PROJECT_DIR/build/Codex Auto Router.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
MODULE_CACHE_DIR="$PROJECT_DIR/build/module-cache"

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR" "$MODULE_CACHE_DIR"
cp "$WIDGET_DIR/Info.plist" "$CONTENTS_DIR/Info.plist"
cp "$PROJECT_DIR/codex_router.py" "$RESOURCES_DIR/codex_router.py"
cp "$PROJECT_DIR/router_config.json" "$RESOURCES_DIR/router_config.json"

xcrun swiftc \
  -parse-as-library \
  -target arm64-apple-macos13.0 \
  -module-cache-path "$MODULE_CACHE_DIR" \
  -framework SwiftUI \
  -framework AppKit \
  "$WIDGET_DIR/Sources/RouterWidgetApp.swift" \
  -o "$MACOS_DIR/CodexAutoRouterWidget"

codesign --force --deep --sign - "$APP_DIR"
echo "$APP_DIR"
