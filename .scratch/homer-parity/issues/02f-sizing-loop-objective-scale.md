# 02f — Sizing loop: the objective scale and dual-reading checks

Status: needs-triage

Follow-up from homer-parity 02a (#1466, GK A A2-1).

#1466 makes the sizing loop reverse the objective scale after each solve (`_rescale_results_for_objective`), so `cost_eur` is in EUR whatever the user or auto scale. Before #1466, `solve_once` reported the scaled objective. Any forward check that read duals or `n.objective` from a scaled round saw scaled values.

## Wanted

Audit the sizing loop's forward checks and the sheet for any value read from the solved network before #1466's reversal. Confirm each now reads EUR or €/MWh, or pin it with a test at scale ≠ 1.
