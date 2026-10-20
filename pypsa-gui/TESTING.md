# pypsa-gui testing rules

Hard-won, mechanical facts. CI runs these suites (`.github/workflows/test.yaml`);
run the focused subset locally while iterating and the full suite at QA gates.

## Backend (pytest)

Run from `backend/` with the repo root on PYTHONPATH:

```bash
cd backend
PYTHONPATH=<repo root> python -m pytest tests/test_<area>.py -q -p no:warnings -n 8 --dist loadfile
```

- **Chat-tool tests that touch the network must bind the session request
  context.** `PyPSAService.get_network()` reads the bound request context;
  without a binding a bare dispatcher call sees the EMPTY process foreground
  (typical symptom: "unknown generator … the network holds 0 generators").
  Use the `bound_ctx` fixture:

  ```python
  def test_x(client, wind_project, bound_ctx):
      with bound_ctx(client) as ctx:
          out = chat_tools.DISPATCHERS["some_tool"](...)
  ```

  The same fixture backs apply-path assertions (`ctx.user_ts`).
- Project-scoped chat tools also need the acting identity: the `client`
  fixture's session provides it; cross-org refusal tests use
  `chat_tools.set_acting_user(other_user_id)`.
- Queue-job tests poll `solve_queue` with a deadline (`_wait_job` pattern in
  `test_chat_tools_profiles.py` / `test_profiles_router.py`).

## Frontend (vitest + tsc)

```bash
cd frontend
npx tsc -b                      # typecheck (blocking)
npx vitest run src/<file>.test.tsx   # one file, ~2 s
```

- **jest-dom matchers are NOT registered.** Assert with
  `element.textContent` + `expect(...).toMatch(...)`; never
  `toHaveTextContent` / `toBeInTheDocument`.
- vitest hangs intermittently when a killed run leaves orphan workers:
  `pkill -f vitest` before a re-run; for long runs redirect to a file and
  poll (`npx vitest run ... > /tmp/v.log 2>&1 & sleep 60; tail /tmp/v.log`).
- Full suite (~5,600 tests, ~2 min) is a QA gate, not an iteration step.
