# Power-architecture comparison: AC / MV UPS / 800 VDC / SST

Status: ready-for-agent
Model: Opus
Type: task
Blocked by: 03
Gated: yes (finance)
Model tier: opus
Size: M

## What to build

- Architecture as a DC template parameter. Each option carries a conversion-loss chain (feeds the effective PUE), capex per MW, and which grid services it can offer (feeds 02).
- A compare view across architectures: energy, cost, grid-readiness verdicts.
- Data rows are "assumed" with sources, in a defaults-pack version (never edit a shipped pack).

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: DC template parameters, defaults-pack version (new version, never edit a shipped pack), compare view in the frontend. Blockers: 03. Reviewers: GK A and GK B (finance); UX for the compare view.

### 2026-11-06: plan verdicts (session_01G7qCuivz5KEGYG3aBcEzgz)

UX (11): GO with changes. Expert shows the "Cheapest over the horizon: X · most grid services: Y" line too; headers in words ("Efficiency to rack", "NPV vs this project (M EUR, <money year>)", "Peak within import limit"); a tooltip with the full name on each service chip; the footnote reads "A screening estimate, with no solve. Outage proof is in Backup.", with the pack version on its own "Source:" line; Guided shows the price used, marked assumed; after a spec change the table reads "out of date", and the section is complete only while the result matches the spec; Compare runs in place; the create-form select's hints are visible text; shots in both modes, one Expert shot at about 1250 px.

GK B (28), finance: GO with K1–K9. Q-B1 (a), Q-B2 (a).
- K1: 2026-11-07 becomes the unpinned default, and the PR lists the call sites. Every 2026-11-06 row is byte-equal in 2026-11-07, and 2026-11-06's hash is unchanged. The new hash is pinned, and so are the tariff currency pins.
- K2: the dc-arch rows are assumed, ranged, money_year 2025, real, output_electric per MW of IT, without grid connection. A test covers the EUR/kW→EUR/MW conversion.
- K3: every row has one boundary (MV bus to rack input, in-rack conversion inside), with no overlap with IT, building or DC-03 batteries.
- K4: the NPV delta is reported at central values and at the range ends. An interval that spans zero reads "not distinguishable"; this is tested.
- K5: the price is the tariff energy component, weighted by the loss hours or the time-mean (named which). Demand, capacity and connection charges are not_valued. The tariff and finance currencies must match. No price → 422. A request price is assumed, and the price is held constant in real terms. Tests: TOU vs mean, no tariff, currency mismatch.
- K6: DC-03's NPV machinery is reused, not forked. The project's own leg is computed. Hand-checked tests cover lifetime < horizon and lifetime > horizon.
- K7: default "ac" is byte-identical. Any LP input from the architecture feeds the fingerprints only when it is not the default; otherwise the PR says the architecture is metadata only.
- K8: the compare route writes nothing (tested). The route policy tests run at the trial merge. Each refusal gets its own error kind in a tool-error-kinds.d fragment.
- K9: each new guard has a test and a mutant that the test kills.

GK A (14) handed off at 300k before verdict; plan re-sent to GK A (15).
