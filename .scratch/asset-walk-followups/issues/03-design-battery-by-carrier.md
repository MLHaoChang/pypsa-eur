# The design battery duration counts every StorageUnit

Status: needs-triage

Raised by GK B3 (3) on #1752 (GK NOT OK @93ccc20b, nit b, accepted as a follow-up).

`designDurationHours` (`pypsa-gui/frontend/src/pages/hubDesign/cheaperDuration.ts`)
takes the one `max_hours` that all built StorageUnits share. A site that also
builds another StorageUnit, e.g. a long-duration hydrogen store, has two
durations, so the cheaper-duration sentence silently does not show.

Ask: count only the battery, i.e. sizing rows with `carrier === 'battery'`, and
keep "null when the battery rows' durations differ". Test with a battery row
plus a non-battery StorageUnit of another duration.
