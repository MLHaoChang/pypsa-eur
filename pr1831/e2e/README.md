# PR #1831 (AA-13) live-app E2E

Run from a session whose environment has a working `PROBE_ANTHROPIC_KEY`. Scripts take the output dir as `argv[1]`.

1. `python build_net.py <dir>/acpf_partial.nc` gives 3 snapshots. On Line_BC (x=20), Load_C is 1, 3 and 200 MW, so AC PF converges at 00:00 and 01:00 and fails at 02:00.
2. Start the API from branch `claude/aa-13-required-warning-slots` (`pypsa-gui/backend`) with this command:
   `ANTHROPIC_API_KEY="$PROBE_ANTHROPIC_KEY" PYPSA_GUI_ANSWER_ASSURANCE_PROFILES=ac-branch-loading/1 PYPSA_GUI_ANSWER_ASSURANCE_MODE=strict /root/.venv-pypsa-gui/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000`
   Start Vite on :5173 with node from /opt/node22/bin.
3. Create the project `acpf_e2e` (the app's new-project flow, signed in as admin@example.com). Then run `python api_setup.py <dir>`, which imports the network, solves LOPF with `run_ac_pf_after_lopf`, and saves `acpf_status.json`. The condition must read `lopf+ac_pf_ok`, with snapshot 02:00 `ok: false`.
4. `node run_chat.cjs <dir>` asks "Show AC branch loading for line Line_BC" and then the all-converged window. It expands the checked-evidence card, shoots light and dark, and saves the receipts as `*_receipt_*.json`. Then run `scripts/coord/shots.sh <png>...`.

Expected: the warning receipt has `policy_version` "5" and `request_complete` false. Its first slot is `ac_pf_convergence` / `warning` (2 of 3 converged). The card reads "Request incomplete: warning" with the row "Warning: AC power flow did not converge for 1 of 3 snapshots in the window. The values cover 2 of 3 snapshots." The clean window gives policy "1" and "Requested results checked".
