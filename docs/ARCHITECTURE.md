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

## 3. Menu bar app

The SwiftUI app reads `~/.codex/auto-router/runtime/state.json` once per second. It can start or stop the watcher but never performs classification itself.

## Interfaces

| Interface | Stability | Use |
| --- | --- | --- |
| Codex lifecycle hooks | Public Codex feature | Prompt-submit interception |
| Local rollout JSONL | Observed implementation detail | Task discovery and telemetry |
| Desktop follower IPC | Internal implementation detail | Next-turn model pre-arm |
| `codex exec resume` | Public CLI command | Same-prompt reroute and continuation |

## Failure behavior

- Hook cannot determine a safe root session: continue without rerouting.
- Attachment detected: continue and show only a recommendation.
- IPC pre-arm fails: keep the active turn, log the failure, notify the user.
- Completed non-goal request without an announced follow-up: do not create a redundant turn.
- Resumption fails: preserve the log and notify the user to retry with the recommended route.
