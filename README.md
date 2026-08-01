# Codex Adaptive Model Router

[![Tests](https://github.com/hadongil19822-blip/codex-adaptive-model-router/actions/workflows/test.yml/badge.svg)](https://github.com/hadongil19822-blip/codex-adaptive-model-router/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![macOS 13+](https://img.shields.io/badge/macOS-13%2B-black.svg)](https://www.apple.com/macos/)

**A zero-token, local model router for Codex Desktop.** It selects Luna, Terra, or Sol and the reasoning effort for each task, then shows every active task in a small macOS menu bar app.

> 한국어 사용자는 [README.ko.md](README.ko.md)를 참고하세요.

## Why

Using the strongest model for every request wastes quota. Always using the cheapest model often creates retries that waste even more. This project estimates task complexity locally and routes each request to the lowest-cost model and effort likely to finish it reliably.

No classifier model is called. Routing uses transparent regular expressions, task length, failures, tool output size, and context pressure.

## Highlights

- **Routes every prompt** through Codex's official `UserPromptSubmit` lifecycle hook.
- **Spends zero model tokens on classification** because all decisions are local.
- **Uses 12 cost bands** across Luna, Terra, and Sol.
- **Pre-arms announced follow-up work** before the next turn starts.
- **Monitors multiple Codex tasks independently.**
- **Protects attachments, explicit model choices, and subagent sessions** from unsafe rerouting.
- **Ships with a native SwiftUI menu bar monitor** for macOS.
- **Keeps routing rules editable** in one JSON file.

## Routing policy

| Workload | Default family | Typical effort |
| --- | --- | --- |
| Short lookup, explanation, formatting | Luna | low–high |
| Everyday editing, implementation, validation | Terra | low–high |
| Complex automation, repeated failures, high-risk work | Sol | low–max |
| Explicit parallel, multi-agent, exhaustive work at the highest score | Sol | ultra |

Ultra is intentionally rare because it can delegate work and consume substantially more tokens. The included catalog reflects the model combinations available in the Codex build used during development; edit `router_config.json` if your installation exposes different models.

## How it works

```mermaid
flowchart LR
    A["User submits a task"] --> B["UserPromptSubmit hook"]
    B --> C["Local rule scorer\n0 model tokens"]
    C --> D{"Current route matches?"}
    D -- Yes --> E["Continue normally"]
    D -- No --> F["Block before model call"]
    F --> G["Resume the same prompt\nwith selected model + effort"]
    H["Agent announces next step"] --> I["Rollout watcher"]
    I --> J["Pre-arm next-turn settings"]
    J --> K["Menu bar status"]
```

The follow-up pre-arm path uses Codex Desktop's local IPC interface. That interface is not a public stability guarantee, so failed pre-arms fall back to notifications and turn-boundary behavior.

## Requirements

- macOS 13 or later
- Codex Desktop / Codex CLI with lifecycle hooks enabled
- Python 3.9 or later
- Xcode Command Line Tools only if you want the optional menu bar app

## Quick start

```bash
git clone https://github.com/hadongil19822-blip/codex-adaptive-model-router.git
cd codex-adaptive-model-router
chmod +x install.sh macos-widget/build-widget.sh codex-auto
./install.sh
```

Codex requires a one-time trust review for user hooks:

1. Open Codex CLI.
2. Run `/hooks`.
3. Review and trust the `UserPromptSubmit` hook.

The installer starts the watcher and places the optional app at:

```text
~/Applications/Codex Auto Router.app
```

## Commands

```bash
./codex-auto watch --all --daemon
./codex-auto status
./codex-auto status --json
./codex-auto decide "Refactor these three files and run the tests"
./codex-auto stop
```

## Customize it

Your live configuration is stored at:

```text
~/.codex/auto-router/router_config.json
```

Change route thresholds, model slugs, supported efforts, cooldowns, notification behavior, or prompt rerouting without editing Python. The installer preserves an existing live configuration on upgrade.

For pattern changes and new task categories, see [Customization Guide](docs/CUSTOMIZATION.md). For internal flow and safety boundaries, see [Architecture](docs/ARCHITECTURE.md).

## Safety and privacy

- Prompt classification happens on-device.
- The router does not send analytics or prompts to a third-party service.
- Prompt text is only written where Codex already stores its local transcript, plus a short-lived `0600` reroute request file that is deleted after resumption.
- Image, audio, and video attachment turns are not automatically resubmitted.
- Subagent and guardian sessions are not rerouted.
- A model explicitly named in the prompt is respected.
- Active turns are not interrupted by the watcher.

Read [SECURITY.md](SECURITY.md) before enabling this on sensitive or production work.

## Current limitations

- macOS only today.
- Model slugs and effort availability can change between Codex releases.
- The menu bar UI is currently Korean-first.
- Prompt rerouting relies on `codex exec resume`; if resumption fails, the router logs the error and sends a recommendation notification.
- This is an independent community project, not an official OpenAI product.

## Contributing

Issues and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md).

If this saves you quota or retries, consider starring the repository—it helps other Codex users discover it. ⭐

## License

[MIT](LICENSE)
