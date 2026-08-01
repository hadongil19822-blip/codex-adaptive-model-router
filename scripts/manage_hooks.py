#!/usr/bin/env python3
"""Install or remove the router's UserPromptSubmit hook without clobbering others."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


HOOK_PATH = Path.home() / ".codex" / "hooks.json"
ROUTER_MARKER = "codex_router.py"
_runtime_script = Path.home() / ".codex" / "auto-router" / "codex_router.py"
ROUTER_COMMAND = f'"{sys.executable}" "{_runtime_script}" prompt-hook'


def load_hooks() -> Dict[str, Any]:
    if not HOOK_PATH.exists():
        return {"description": "User-level Codex lifecycle hooks.", "hooks": {}}
    try:
        value = json.loads(HOOK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Cannot update invalid JSON at {HOOK_PATH}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Expected a JSON object at {HOOK_PATH}")
    value.setdefault("hooks", {})
    return value


def is_router_handler(handler: Any) -> bool:
    command = str(handler.get("command") or "") if isinstance(handler, dict) else ""
    return ROUTER_MARKER in command and "prompt-hook" in command


def backup_existing() -> None:
    if not HOOK_PATH.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(HOOK_PATH, HOOK_PATH.with_name(f"hooks.json.backup-{stamp}"))


def atomic_write(value: Dict[str, Any]) -> None:
    HOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = HOOK_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, HOOK_PATH)


def install() -> None:
    value = load_hooks()
    event_groups = value["hooks"].setdefault("UserPromptSubmit", [])
    for group in event_groups:
        for handler in group.get("hooks", []):
            if not is_router_handler(handler):
                continue
            if handler.get("command") == ROUTER_COMMAND:
                print(f"Router hook is already installed: {HOOK_PATH}")
                return
            backup_existing()
            handler["command"] = ROUTER_COMMAND
            handler["timeout"] = 5
            handler["statusMessage"] = "Selecting the best model for this task"
            atomic_write(value)
            print(f"Updated router hook: {HOOK_PATH}")
            return
    backup_existing()
    event_groups.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": ROUTER_COMMAND,
                    "timeout": 5,
                    "statusMessage": "Selecting the best model for this task",
                }
            ]
        }
    )
    atomic_write(value)
    print(f"Installed router hook: {HOOK_PATH}")


def uninstall() -> None:
    value = load_hooks()
    groups = value.get("hooks", {}).get("UserPromptSubmit", [])
    changed = False
    retained_groups = []
    for group in groups:
        handlers = group.get("hooks", [])
        retained_handlers = [handler for handler in handlers if not is_router_handler(handler)]
        if len(retained_handlers) != len(handlers):
            changed = True
        if retained_handlers:
            updated = dict(group)
            updated["hooks"] = retained_handlers
            retained_groups.append(updated)
    if not changed:
        print("Router hook is not installed.")
        return
    backup_existing()
    if retained_groups:
        value["hooks"]["UserPromptSubmit"] = retained_groups
    else:
        value["hooks"].pop("UserPromptSubmit", None)
    atomic_write(value)
    print(f"Removed router hook: {HOOK_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    args = parser.parse_args()
    install() if args.action == "install" else uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
