# A stale run serves the figures it was solved with

Status: needs-triage
Model tier: opus.
Size: L.
Blocked by: 13 (PR #1960).
Gated: GK, UX.
Likely files:
- `pypsa-gui/backend/routers/results.py` (`_dispatch_ready`, `_not_solved`, the results readers)
- `pypsa-gui/backend/services/asset_results/compute.py`, billing, value flows
- the solve's saved inputs (a snapshot of the static inputs or of the derived tables at solve time)
Type: feature

After issue 13 a value edit keeps the last run, which reads `stale`; its results readers withhold their figures with a typed "out of date, re-run" (`X-Results-State: out_of_date`), because the kept dispatch (`_t` tables) would otherwise be recombined with the edited static inputs (KPI system cost 3500 against a run objective of 2800, a loss no run produced, a moved load keeping the old flow). The owner chose withholding (owner decision PG13-STALE-1) and asked for the real fix as a follow-up.

## What to build

Serve the run's own figures, computed from the inputs at solve time, with the stale label, so a user can still read what the last run said after editing a value. Either snapshot the static inputs the readers use at solve time, or snapshot the derived tables; the readers (about ten results routes, billing, value flows, asset results) then read the snapshot while the run is stale.

## Acceptance criteria

- [ ] After a value edit (marginal cost) and a topology edit (a load's bus), the KPIs, asset economics, lines and energy flows serve the last solve's figures, labelled out of date; none reads today's static inputs.
- [ ] A re-run serves current figures again; the snapshot is saved with the project and survives a reopen.
- [ ] Remove the withholding added by issue 13 for the readers that serve the snapshot.
- [ ] Real app, IPP template: edit the battery's max power, Results shows the last run's numbers with the "Out of date" band.
