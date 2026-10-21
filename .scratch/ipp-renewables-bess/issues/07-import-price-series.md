# Wholesale-indexed import price (`import_price_ref`)

Status: needs-triage
Type: task
Blocked by: —
Gated: **yes** (LP binding, billing and the IC ledger)
Model tier: opus
Size: M

Read [the spec](../spec.md) §2.3 and §10 G7 first. Owner (2026-10-20, about 20:00 CEST): built in this
package, before the template ships, so the battery charges from the grid at DA and arbitrage is
valued correctly.

## What to build

The mirror of `export_price_ref`:

1. **Model.** `CommercialConfig.import_price_ref: TimeSeriesRef | None = None`, in
   `hashing.FIELDS_AFTER_V1`. The validator refuses it without an import Link (`poc_link`). It is
   pinned in the bundle like the export series.
2. **Binding.** `bind_commercial` aligns it like the export series (full coverage or refusal). It is
   written as a links_t column on the import Link(s), with the same snapshot-axis guard.
3. **LP.** `adders["import"] += price`, an `lp_input`. `circulation_risk_snapshots` includes it,
   so a network-charge item > 0 keeps it at 0.
4. **Billing and ledger.** A wholesale import cost line Σ w · p(import) · price, from the market to
   the site party, booked once beside the tariff's own items. Its stream and escalation class are
   the gatekeeper's call (proposed: the market-price class, as the export).
5. **Preflight.** Coverage, and the existing arbitrage-loop warning with the series in.
6. **Editor** (UX reviewer, 2026-10-20). Today no editor sets the export price either, so a
   "Prices the optimisation uses" block holds both Library series pickers: export (shown with an
   export Link) and import. Each shows its provenance (id, version, `illustrative`). The block
   sits in today's tariff editor (`TariffSection`, the 7a host K1 moves), above the import
   tariff, so both of its mounts (Economics › Tariffs & contracts, Results › Investment case ›
   Tariff) show one editor. Both are **`lp_input`** (#447 §2.0): shown with the Commercial group
   off. Guided reaches them with W3c's 7a card; until then the assistant sets either series
   through the LC-04 edit card.

## Acceptance

- A toy year with a battery: it charges from the grid only when DA + charges < a later DA. The
  IC's import cost equals Σ w · import · (DA + charge).
- Hashes don't move for configs without the field.
- Without the field, results are byte-identical.
