<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G-b: real application evidence

These 1440 × 900 captures use the actual authenticated FastAPI/React application,
an owned Energy Hub data-centre project, a native 168-hour model week, real job,
comparison and apply routes, and **synthetic weather only**. No model calls,
ERA5/CDS requests, HTTP response mocks or application-store injection are used.
`g-real-app-evidence.json` records the source head, checks and deliberate fault.

| Capture | State |
|---|---|
| g-expert-renewables.png | Expert per-row Tune placement beside Generate |
| g-weather-window.png | Sampled-window line, dates, hours and UTC |
| g-tune-untouched.png | Native schema defaults, units and provenance |
| g-era5-disabled.png | Visible source-disabled field explanations |
| g-tune-changed.png | Changed field, per-run note and Reset to preset |
| g-json-error.png | Plain refusal for non-object Advanced JSON |
| g-loading.png | Actual native comparison request still loading |
| g-disabled-apply.png | Apply disabled with a visible explanation |
| g-compare-week.png | Current/New week and duration curves |
| g-compare-statistics.png | Native statistics and Change column |
| g-out-of-date.png | Edited settings invalidate an existing preview |
| g-refusal.png | Transport refusal, Retry and collapsed Details |
| g-apply-receipt.png | Native single-apply receipt |
| g-new-only.png | Missing current series: New only, no fabricated baseline |
| g-guided-renewables.png | Guided drawer, no Tune entry |

The refusal capture deliberately blocks the comparison URL in Chromium, then
unblocks it and retries the real request. This is a transport fault, not a
mocked server refusal. The native apply receipt confirms 168 hours and exactly
one additional undo step. The new-only case checks the real response has
`a=null`, `delta=null`, `week.a=null`, and that the browser omits Current/Change.
The comparison uses the model clock; it does not invent a UTC basis. Only the
separate sampled-window line uses the UTC convention required by the brief.

## Reproduce

Use an isolated development app and a disposable, authenticated account with
permission to create and lock projects. The walk creates/reuses
`G-b profile tuning review`, places its PV bus, selects synthetic weather and
writes a profile through ordinary native routes. Do not point it at production
or a project you want to preserve. Run from the repository root with
`playwright-core` and Chromium available. The app must serve `/src/` via Vite
and proxy `/api` to its actual backend.

```sh
GRIDSPINE_BASE_URL=http://127.0.0.1:15173 \
GRIDSPINE_AUTH_STATE=/private/path/to/authenticated-storage-state.json \
GRIDSPINE_EVIDENCE_DIR=/tmp/gridspine-gb-evidence \
GRIDSPINE_PLAYWRIGHT=/path/to/playwright-core/index.mjs \
GRIDSPINE_CHROMIUM=/usr/bin/chromium \
node .scratch/renewable-profiles/evidence/g-tuning-walk.mjs
```

`GRIDSPINE_PLAYWRIGHT` defaults to installed `playwright-core`. Authentication
state, CSRF cookies, fixture project data and logs remain in the private output
directory. Never add those files to Git. Only reviewed PNGs and the non-secret
`real-app-evidence.json` manifest belong with this evidence.

The walk uses the existing public `openTimeSeriesFor` navigation action and
visible app controls. It waits for project activation before navigating; it
never injects a fixture into the application store. All numerical data comes
from native routes. This is a reproducible evidence walk, not a new browser test
framework or a claim of domain approval.
