Screenshots for PR #727 @1f6d7c88 (g2p 17a PR 2, flicker screen), Guided and Expert. All data is the PR backend's own output (data.json, made by dump_test.py):
- the Link asset schema and links through the API test client on the test_process_harmonic_fields network: "heater" with its flicker inputs, "compressor" with no step, and "conveyor", which is not a process unit;
- grid_readiness() on tests/test_grid_readiness.py::_flicker_site with the generic EU RfG/DCC Continental Europe profile;
- a grid-code draft holding that shipped profile.

1a: the Link edit card with Process unit off. No flicker fields show.
1b: the same card after ticking Process unit. Under Technical performance, "Largest power step (MVA)" and "Changes per minute (1/min)" appear empty, next to the harmonic fields.
1c: the heater filled in (12.5 MVA, 4 changes per minute).
2: the grid-code draft table's two new rows, "Flicker severity Pst at the PCC" 0.8 and "Plt" 0.6. Both are tagged ASSUMED, with the planning-level clause.
   This PR also makes the by-order harmonic row wrap. Before, its ~40 orders on one line made the table about 3800 px wide and pushed every row's clause out of view.
3: the scorecard's flicker row with its detail open, in four states:
   - pass (step 10 MVA: Plt 0.448 against 0.6);
   - warn (20 MVA: Plt 0.896 against 0.6);
   - unknown, with "compressor: Largest power step is not set" under the title;
   - unknown, with "No short-circuit level at the point of connection".
   Plt equals Pst in this screen, so the row shows the binding limit: Plt 0.6, not Pst 0.8.
4 (Expert only): "Go to field" on the no-step row opens the compressor's Link card. Its Largest power step is empty, and Changes per minute reads 2. The harness's jump opens the target unit's card, as the app's jump opens its component.

Guided vs Expert:
- The Link card and the grid-code table do not branch on the UI mode, so 1x and 2 are identical across modes.
- In Guided, the scorecard shows "Ask the assistant" (disabled: the harness has no assistant) and "Editable in Expert: Assets & sizing › compressor › Largest power step".
- In Expert, it shows "Go to field". The no-S_k row has no field to go to, because its target entity is unset; this is the same as the harmonic row.

Rerun:
1. Copy harness.html and harness.vite.config.ts to pypsa-gui/frontend/, and harness.tsx to pypsa-gui/frontend/src/.
2. Run `npx vite --config harness.vite.config.ts` (port 5199).
3. Run `OUT=<dir> node shoot.mjs`.
4. Delete the copies afterwards.
