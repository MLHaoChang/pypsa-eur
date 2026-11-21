# The design battery duration counts every StorageUnit

Status: done

Raised by GK B3 (3) on #1752 (GK NOT OK @93ccc20b, nit b, accepted as a follow-up).

`designDurationHours` (`pypsa-gui/frontend/src/pages/hubDesign/cheaperDuration.ts`)
takes the one `max_hours` that all built StorageUnits share. A site that also
builds another StorageUnit, e.g. a long-duration hydrogen store, has two
durations, so the cheaper-duration sentence silently does not show.

Ask: count only the battery, i.e. sizing rows with `carrier === 'battery'`, and
keep "null when the battery rows' durations differ". Test with a battery row
plus a non-battery StorageUnit of another duration.

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still true: `cheaperDuration.ts` filters on StorageUnit only. Acceptance as in the Ask, with the test it names. Gated: GK B3. Blocked by: none.

## Done

Status: done (PR pending merge). `designDurationHours` filters `carrier === 'battery'`; test with a battery plus a non-battery StorageUnit of another duration added.
