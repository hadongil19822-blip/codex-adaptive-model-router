# Architecture

The project has three independent paths. A failure in the UI does not stop routing, and a failure in next-turn pre-arming does not modify the active turn.

## 1. Prompt-submit router

Codex invokes the user-level `UserPromptSubmit` hook before the prompt is sent to a model. The hook:

1. Rejects subagent sessions and explicit model choices.
2. Scores the text locally.
3. Reads the active model and effort from the rollout tail.
4. Continues immediately when the route already matches.
5. Otherwise writes a `0600` request file, blocks the original model call, and starts a detached helper.
6. The helper waits briefly and runs `codex exec resume` with the same prompt and selected route.

`CODEX_AUTO_ROUTER_RESUBMIT=1` prevents the resumed prompt from entering a loop.

Attachment-bearing turns are not automatically resubmitted because the hook input does not carry a stable, complete attachment transfer contract.

## 2. Rollout watcher

One background process discovers recent root user rollouts under `~/.codex/sessions`, excluding guardian and subagent sessions. Each root task has an independent observer and telemetry record.

The observer tracks:

- current model and effort
- active/completed turn boundaries
- latest user request
- announced next step
- failures and large tool outputs
- context-window pressure
- cumulative token counters reported by Codex

When an assistant announces a valid future action, the route is classified once and locked for that next step. This avoids model ping-pong as token counters update.

## 3. Desktop dashboards

The macOS SwiftUI app and Windows PowerShell/WinForms tray app read `~/.codex/auto-router/runtime/state.json`. They can start or stop the watcher and edit usage-guard settings but never perform classification themselves.

## 4. Weekly usage guard

The watcher requests `account/rateLimits/read` from the local Codex app-server every five minutes and selects the longest Codex quota window. This is a local account-status request, not a model request, so it consumes no model tokens.

The guard is opt-in. At or below the configured remaining percentage:

1. Active turns continue to a safe boundary.
2. Next-turn pre-arming and automatic follow-ups stop.
3. The prompt-submit hook blocks new root-user prompts.
4. Work becomes available again after the reported quota rises above the threshold or the guard is disabled.

## Interfaces

| Interface | Stability | Use |
| --- | --- | --- |
| Codex lifecycle hooks | Public Codex feature | Prompt-submit interception |
| Local rollout JSONL | Observed implementation detail | Task discovery and telemetry |
| Codex app-server | Documented local protocol | Usage status and cross-platform next-turn settings |
| Desktop follower IPC | Internal macOS fallback | Next-turn model pre-arm compatibility |
| `codex exec resume` | Public CLI command | Same-prompt reroute and continuation |

## Failure behavior

- Hook cannot determine a safe root session: continue without rerouting.
- Attachment detected: continue and show only a recommendation.
- IPC pre-arm fails: keep the active turn, log the failure, notify the user.
- Completed non-goal request without an announced follow-up: do not create a redundant turn.
- Resumption fails: preserve the log and notify the user to retry with the recommended route.
