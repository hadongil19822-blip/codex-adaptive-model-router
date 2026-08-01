# Customization Guide

The live configuration is `~/.codex/auto-router/router_config.json`. The repository copy is the default used for a first installation.

## Route table

Routes are evaluated from top to bottom. The first route whose `max_score` contains the task score is selected.

```json
{
  "max_score": 2,
  "tier": "terra_low",
  "model": "gpt-5.6-terra",
  "effort": "low"
}
```

The last route can require an explicit parallel-work signal:

```json
{
  "max_score": 999,
  "tier": "sol_ultra",
  "model": "gpt-5.6-sol",
  "effort": "ultra",
  "requires_parallel_signal": true
}
```

## Default scoring

- Simple lookup or explanation: `-1`
- Normal edit, implementation, test, or validation: `+2`
- Complex automation, bulk work, or architecture: `+4`
- Security, data loss, or destructive-risk work: `+8`
- Medium or long prompt: `+1` or `+2`
- Repeated failures: `+3`
- Large tool output: `+1`
- Context pressure: `+1`

A phrase may match multiple groups. This is intentional: “implement a real-time security router” should score higher than either signal alone.

## Add another language

Edit the pattern tuples near the top of `codex_router.py`:

- `SIMPLE_PATTERNS`
- `NORMAL_PATTERNS`
- `COMPLEX_PATTERNS`
- `CRITICAL_PATTERNS`
- `PARALLEL_PATTERNS`

Add unit tests that cover both expected upgrades and expected downgrades.

## Disable automatic resubmission

Keep recommendations and next-turn monitoring but never block and replay a user prompt:

```json
"prompt_submit_reroute": false
```

## Weekly usage guard

The guard is disabled by default. It can be changed from either desktop dashboard or directly in JSON:

```json
"usage_guard": {
  "enabled": true,
  "pause_at_remaining_percent": 10,
  "max_cache_age_seconds": 300,
  "query_timeout_seconds": 3,
  "mode": "safe_turn_boundary"
}
```

Safe-boundary mode does not interrupt an active edit or tool call. It blocks new prompts and automatic continuation after current turns finish. `usage_poll_interval_seconds` controls background refresh frequency; lowering it does not use model tokens but does create more local account-status requests.

## Use different model slugs

Replace the `model` fields in `routes` and update `model_capabilities`. The router clamps an unsupported effort to the highest effort declared for that model.

## Exclude a task

Add a root Codex task ID to `excluded_thread_ids`. Exclusions apply only to the rollout watcher; the global prompt hook follows its own root-session safeguards.
