<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Mutation: hold the locks until the generator ends

GK B condition 2 deviation evidence (issue 02, PR #847). The runner releases
`mutation_lock` and `solver_state_lock` on `tool_running`, because the handler
runs on `_TOOL_EXECUTOR` and takes the project's lock itself.

## Mutation

`pypsa-gui/backend/harness/runner.py`, `Runner.dispatch`:

```diff
                 elif kind == "tool_running":
-                    locks.close()  # the handler takes its own locks on the tool executor
+                    pass  # MUTATION: hold the locks until the generator ends
```

## Result

`pytest tests/test_harness_runner.py -k carded_effectful` (the test that
patches `LOCK_TIMEOUT_SECONDS` to 2 s and wraps the handler in
`PyPSAService.get_lock()`) **fails**: the first effectful step's handler
(`input_gate.1`, `validate_network`) blocks on the lock the runner holds
and ends `failed` with `error: tool_timeout`, the run goes `needs_user`
(`reason: step_failed`), and it never reaches the solve card:

```
E       AssertionError: {... 'id': 'input_gate.1', 'tool': 'validate_network', ...
         'status': 'failed', ... 'error': 'tool_timeout', ...
         'run': {... 'status': 'needs_user', 'stage': 'input_gate', ... 'reason': 'step_failed' ...}}
```

Without the mutation the same test passes and also asserts the handler ran
on a `chat-tool` executor thread bound to the run's own `ProjectContext`
(`RUN_CONTEXT` and `get_active_context()`), per GK B addition (a).
