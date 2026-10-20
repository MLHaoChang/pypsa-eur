# Revenue by stream and curtailment by cause

Status: needs-triage
Type: task
Blocked by: 03; placement in 10 › Commercial needs K1/W10
Gated: told (results reader; no engine change); UX
Model tier: sonnet
Size: M

Read [the spec](../spec.md) §5 and §10 G6 first.

## What to build

- A results reader that groups the IC's `CashflowLine`s by stream per year: merchant (export), PPA,
  CfD difference, import cost, O&M, replacement.
  - The IPP-sold financial PPA (ticket 01) is re-labelled as "PPA revenue = price × share × gen"
    and "merchant = ref × (1 − share) × gen".
  - The sum of the streams equals the IC's revenue (test).
- Curtailment split by cause, per plant: export at the limit vs price < 0 vs both. The causes sum to
  the existing curtailment total (test).
- Capture price and capture rate, and the battery's average charge and discharge price, read from
  `asset_results`. Nothing is recomputed.
- The UI in 10 › Commercial, after the UX reviewer's placement note.
