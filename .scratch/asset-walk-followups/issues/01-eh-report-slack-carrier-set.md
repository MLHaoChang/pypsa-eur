# eh_report's slack-carrier set disagrees with slack.py

Status: done

Raised by GK A 30 on #1677 (GK OK @bb7fb4fe); older than that PR, not blocking it.

`services/adequacy/eh_report.py` keeps its own `_SLACK_CARRIERS`
(`voll`, `load_shedding`, `dsr`, `demand_response`), used by
`sizing_summary_from_network` (carrier totals) and `sized_assets` (the
sized-asset rows). `services/adequacy/slack.py` has the canonical
`SLACK_CARRIERS`, which also holds the current builds' carriers:
`voll_slack` (INVOLUNTARY_SLACK_CARRIER) and `dc_curtailment`
(DC_CURTAILMENT_CARRIER).

Effect: a slack generator added under `voll_slack` or `dc_curtailment`, and
not named `__voll*`, counts in the EH report's carrier totals and appears
as a sized asset.

Fix sketch: read `slack.SLACK_CARRIERS` (plus the `demand_response` /
`load_shedding` aliases if old networks still carry them) in `eh_report`,
or use slack.py's own slack predicate if it has one. Test: a generator on
each `slack.SLACK_CARRIERS` carrier is neither in `by_carrier` nor a row.
The EH report fixtures and fingerprint tests must stay unchanged.

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still true: `eh_report.py:50` keeps its own `_SLACK_CARRIERS` without `voll_slack` and `dc_curtailment`. Acceptance: it uses the canonical `SLACK_CARRIERS` from `adequacy/slack.py`, with a test that a generator under `voll_slack` and one under `dc_curtailment` are excluded from the sized assets and totals. Gated: GK A. Blocked by: none.
