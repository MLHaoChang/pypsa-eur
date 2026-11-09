# A DC-priced cost does not go stale when the generator's DC/AC ratio changes

Status: done (#1281, #1293)
Type: gap
Found in: TC-02 (issue 02) review condition 6 by GK A, 2026-11-06.

Issue 02 scales an `output_electric_dc` overnight cost by the generator's `dc_ac_ratio` and records the ratio it priced
with on the overnight cost's provenance (`dc_ac_ratio`). Nothing flags the cost when the ratio changes afterwards.
There is no existing staleness check to join: a Link's eta-scaled pack price is not flagged when its efficiency
changes either. Decide one mechanism for both (compare the recorded scale input with the current one, and show the
cost as stale on the asset card), rather than one per field.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: overnight-cost provenance (`dc_ac_ratio`), eta-scaled Link price, asset card.
- Blocked by: none.
- Model tier: opus.
- Gated: GK A; plan to GK A first.
- Size: L.
- One mechanism for both fields (recorded scale input vs current); L: the builder proposes a split.

### 2026-11-08: plan GO, GK A (18), with conditions

Split: S1 backend (GK A): pricing and reference apply record the scale input (`scaled_by`), `provenance.staleness`
compares it with the current one, both provenance routes return `stale`. S2 frontend (UX): the provenance chip shows
stale on the asset card and the Asset costs table.

- T5-1 One reader: `staleness` recomputes the current scale input through pricing's own `_scale_for` (column and
  value); a Link whose priced output moves from bus1 to bus2 reads stale. A refusal now reads stale with current None,
  and no exception reaches the route.
- T5-2 Links scale by the static efficiency column only; a test with a `links_t.efficiency` series pins it.
- T5-3 An entry priced by a scaling rule that recorded no input reads `stale: {field: null, reason: "scale input not
  recorded"}`, never fresh. Unit-rule entries read no stale. The legacy `dc_ac_ratio` key reads as `scaled_by`.
- T5-4 Every provenance writer listed; a customised write through the equipment-table bulk path and through
  `update_component` leaves no `scaled_by`.
- T5-5 The PR body names each raw reader left raw (preflight.py, reference_apply.py, study/asset_view.py) and why.
- T5-6 `scaled_by` is new; provenance without it reads byte-equal through `provenance.get`; the pricing pins stay
  unchanged; tolerance math.isclose rel 1e-9.
- T5-7 Mutants: column compare, value compare, refusal branch, not-recorded branch, legacy-key read, mark_customised's
  drop; each killed by its own test.
