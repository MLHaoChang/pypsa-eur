# Plain Island Microgrid: its own investment case

Status: ready-for-agent

## Brief (coordinator, 2026-10-19)

Give the plain Island Microgrid template (eh_microgrid) its own investment case, so the Investment check gives a real NPV instead of opening on an empty "Site connection" form.

- Commercial block: poc_link subsea_tie, UTC, tariff only if needed.
- Finance block for the plain network: its own candidates (diesel_new 12 MW, bess_new 15 MW), degradation keys and value flows built from the plain network, not copied from the staged one.
- A single-year horizon rule: how one representative week scales to a year and to the money years, consistent with what the engine already does for single-period cases.
- Every number reuses the staged microgrid's cost/finance assumptions where the asset is the same, else an existing defaults pack; a number with no source is an owner question.
- Template data only, plus any small finance-builder change the gatekeeper agrees to.
- Unchanged: staged microgrid NPV 43,231,055, DC −7,992,548, H2 +14,595,554, generic 18,642,945.
- No overlap with UX PR #385 (island sidecar for eh_microgrid in eh_staged_hubs.py).

## Gatekeeper conditions (go, 2026-10-19)

1. Money years stated correctly: inputs in currency_year 2020; base_year 2030 is the first operating year; show that line money years and escalation start match the staged microgrid's.
2. Builder change regression: the staged microgrid's sidecars are byte-identical, its NPV is still 43,231,055, the plain objective is still 3,473,806, and every suite that builds the plain microgrid passes unchanged.
3. Ledger and study notes in TEMPLATE_META["eh_microgrid"], each with its source: the single-year rule; one COD date and the close; the existing island sunk; island rows off, so break-even is not applicable; the inverter re-bought at 10 years (irr_multiple_sign_changes).
4. Test (a) reads the owned assets from the plain network and value flows, not a hard-coded list.
5. Test (b) is live_solve and slow, with the NPV pinned at rel 0.01 and the counterfactual flags checked.
6. Tests first in commit order; the mp-code-review result goes in the PR body; one release-note fragment; no change to eh_staged_hubs.py.
