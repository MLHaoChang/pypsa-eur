# `output_electric_ac` vs `output_electric_dc` rows: price onto the right rating

Status: done (claude/tc02-dc-ac-ratio)
Type: bug (latent)
Found in: GS-2b (typed cost units) review by the engine gatekeeper, 2026-10-21.

`pricing.SCALING_RULES` treats `output_electric_ac` and `output_electric_dc` like `output_electric`. A
DC-rated PV row (per MW of DC) priced onto a Generator whose `p_nom` is the AC rating misprices by the
DC/AC ratio, and an AC row onto a DC-rated asset the other way. Today every shipped PV row is
`output_electric_ac`, so nothing is wrong in practice.

To decide: the asset has no field that says which rating its `p_nom` is. Options are a refusal for
`output_electric_dc` rows until the asset schema carries the rating, or a DC/AC ratio on the asset.
Needs the engine gatekeeper's call on where the rating lives.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role needs-info

Was: `needs-triage`. Owner question Q-TC-2 (GK A decides where the rating lives). Latent: every shipped PV row is AC. (1, Recommended) Refuse `output_electric_dc` rows in `pricing.SCALING_RULES` until the asset schema carries the rating: a few lines, a clear message, no schema change. (2) Add a DC/AC ratio field on the asset and scale by it: correct for DC rows, but a schema change across the generator model. (3) Leave it until a DC row ships: no work now, the risk stays silent. Once answered (1): ready-for-agent, Sonnet, file `pricing.py` and its tests, reviewer GK A.

2026-11-06, owner answer Q-TC-2 (coordinator card): "Add DC/AC ratio field": the asset carries a DC/AC ratio and DC rows scale by it. Role ready-for-agent; Opus (schema change), reviewer GK A (where the rating lives).

### 2026-11-06: GK A design GO (TC-02), rulings

- Where the rating lives: a Generator's `p_nom` is its AC rating; `dc_ac_ratio` = DC module rating / `p_nom`, declared
  only (NaN = not declared, never assumed 1), a lazy column on the `PROCESS_CUSTOM_COLUMNS` / `GENERATOR_LAZY_COLUMNS`
  machinery.
- (a) Bounds (0, 3]; above 3 the message names a typed percentage ("125 % is 1.25"); below 1 is allowed.
- (b) The AC `dc_ac` confirmation stays as it is here; its text goes stale once `p_nom` is AC: gap issue 04.
- (c) New error kind `dc_ac_ratio_missing`, in its own `pypsa-gui/tool-error-kinds.d/` fragment.
- Conditions: registered end to end (FieldSpec, model, schemas, attrs_doc, fieldLabels, frontend fixture; `p_nom`
  help states the AC rating); every write path runs the same check, blank clears to NaN; lazy column, fingerprint
  unmoved; the LP never reads it; `_rule_basis` keeps folding only `_ac`, explicit `_dc` rows for every component;
  provenance records the ratio priced with. No staleness check exists for a scaled price (a Link's eta-scaled price
  is not flagged either): gap issue 05.
