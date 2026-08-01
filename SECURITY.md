# Security

## Reporting

Please report suspected vulnerabilities through GitHub's private vulnerability reporting feature instead of opening a public issue.

## Trust boundary

The router reads local Codex rollout files, installs a user-level lifecycle hook, talks to Codex Desktop's local IPC socket, and can invoke `codex exec resume`. Treat it as local automation with the same access as your user account.

Before trusting the hook, inspect:

- `hooks.json`
- `codex_router.py`
- `scripts/manage_hooks.py`

Codex intentionally requires a one-time `/hooks` trust review. Do not bypass that review in shared or managed environments.

## Sensitive prompts

Routing is local and no telemetry is sent by this project. During rerouting, the prompt is stored briefly in a user-only (`0600`) request file under `~/.codex/auto-router/runtime/prompt-routes/` and deleted after the resumed command exits.

Disable `prompt_submit_reroute` for environments where any temporary prompt copy is unacceptable.

## Experimental interface

Next-turn pre-arming uses an internal Codex Desktop IPC method. It may change without notice. Keep `fallback_notifications` enabled and test after Codex upgrades.
