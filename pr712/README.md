<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PR #712: current real-app UX evidence

Source head: **1f7a44ba43ef3dc7a9d4114ce293358bbc3ff329** on `claude/asset-footprint-parameters`.
User-relayed review: https://github.com/MLHaoChang/pypsa-eur/pull/712#issuecomment-6422564547 (UX NOT OK on previous c117493f).

## Required screenshot matrix

| Mode | Properties collapsed | Properties open, library estimate | Site 3D, selected battery, one editor |
|---|---|---|---|
| Guided | [collapsed](guided-properties-collapsed.png) | [open](guided-properties-open.png) | [Site](guided-site.png) |
| Expert | [collapsed](expert-properties-collapsed.png) | [open](expert-properties-open.png) | [Site](expert-site.png) |

Additional actual-app evidence:

- [Guided saved](guided-properties-saved.png) and [Expert saved](expert-properties-saved.png): 12.5 × 5.8 m, height 3 m, area 72.5 m², no omitted-height hint; safety disclaimer remains.
- [Guided drawer and expanded dock](guided-site-drawer.png): one foreground editor; background Properties and Site do not duplicate it.
- [Expert overlay fallback](expert-site-overlay.png): one editor over the canvas when Properties is hidden.
- [Guided read-only](guided-readonly.png) and [Expert read-only](expert-readonly.png): actual second test identity holds the native project lock. Both actions are disabled with computed opacity 0.5 and the previous saved message is absent.

These render the **real authenticated React application and native FastAPI backend**, not a synthetic component card or intercepted sites API. The project is an isolated illustrative data-centre template; fixture coordinates are not a surveyed customer site. `capture.mjs` drives normal UI clicks for the required shots. Drawer/lock edge cases invoke existing public app navigation/lock actions after normal authentication, not direct store injection. `real-app-browser.json` records the exact source head, 12 PNGs, assertions and zero page errors. Credentials, cookies and session files are not published.

The visible Overpass HTTP 502/no imagery line is an actual external-source availability limitation. Native 3D assets/proxies and the editor render normally; this evidence does not certify GIS content, photorealism, dynamics, paid providers or packaged-device performance.

## Validation

- `frontend-final.json` / `frontend-final.log`: **940 passing tests across 95 relevant frontend files**, zero failed/pending tests. Includes editor, Site/layout/store/API, Properties, all existing App tests and FieldDrawer/flow-navigation regressions. This is scoped regression validation, not all 557 frontend test files.
- `tsc-final.log`: `tsc -b` completed with exit 0; empty output.
- Editor checks cover blank/populated height, safety text, read-only/late-save status, project/selection changes, colon copy, precise validation and rounded display without changing stored geometry.
- `site-duplicate-red.log`/`site-duplicate-green.log`: actual app had two editors before the first correction and one after it.
- `drawer-red-current.log`/`drawer-green.log`: Guided component drawer plus expanded dock had two editors before centralized ownership and one after it.
- `backend-final.xml` is **historical c117493f evidence** (357 passed/one existing empty-parameter skip), not a new backend run. No backend code changed in this UX follow-up. Do not sum old and new counts.
- Both local advisory axes cleared the code at the current head; Spec additionally visually accepted all six required screenshots and the extra drawer/overlay/read-only states. These are not official UX/GK verdicts.

Current remote snapshot: CodeQL 40373202818 and release guard 40373202850 passed; Unit executed successfully and frontend Type-check passed. Test 40373202777 is still pending. See remote-test-jobs.json and remote-codeql-jobs.json. Current premerge.log is bound to the new head/master and reports a clean merge/no overlap, with CI and the prior UX NOT OK still blocking. Applicable exact-head remote CI is recorded in the PR body and handoff as it completes. Old c117493f CI does not certify this new head. Official UX/GK re-review is pending; the coordinator owns merge. Probe text logs normalize trailing whitespace only; original local stdout remains under `/workspace/scratch/pr712-ux-20261026`.

## Historical evidence

`historical-component/` retains the previous synthetic-card images, browser record, frontend results, old README/handoff, remote job list and premerge record for c117493f. They are archived development/review evidence and are **replaced by the real-app matrix above** for the current UX review.

No paid model/media call, model training, unrelated PR change, reviewer/publisher contact, production activation or merge occurred.
