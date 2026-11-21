# The sized-asset rows carry no unit

Status: ready-for-agent

Raised by GK B3 (3) on #1734 (GK NOT OK @79ec862b, point 1); the frontend half is fixed in #1734.

`eh_report.sizing_summary_from_network`'s `assets` rows name their sizes
`built_mw` / `built_mwh` and the bounds `min_mw` / `max_mw` (`min_mwh` /
`max_mwh` for a Store), whatever the bus. On a tonne bus the engine's size
is t/h (t for a stock); `flow_service._size_unit` already knows this for
section 5's held rows.

Today the frontend (`SizedDesignTable`, `holdWords.heldFigure`) takes each
asset's unit from the flow's section 5 rows (`FlowHeld.unit`) and shows no
unit where the flow does not list the asset (past `HOLDS_CAP`) or the bus
unit is not known.

Fix sketch: each `assets` row carries `unit` (its power unit, or for a
Store its stock unit) from `flow_service._size_unit`, and the frontend reads
it before the flow's. Test: an NH3 Store on a tonne bus reads `unit: "t"`;
an energy-bus StorageUnit reads `unit: "MW"`. The EH report fixtures and
fingerprint tests must stay unchanged (display-only key).

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still true: `sizing_summary_from_network` still names every size `built_mw`/`built_mwh`, whatever the bus. Acceptance: each sized row carries its own unit (t/h, t for a stock, MW, MWh), using `flow_service._size_unit`; the frontend takes the unit from the row, not only from the flow's section 5. Gated: GK B3. Blocked by: none.
