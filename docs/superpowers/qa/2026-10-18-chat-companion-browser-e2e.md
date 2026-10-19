<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Chat companion — actual browser/backend acceptance

Date: 2026-10-18, Europe/Berlin
Application source: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Outcome: **6 scenarios passed / 2 failed**. This journey is not fully green.

## Test boundary

Native headless Chromium used the actual built React application and an actual
Uvicorn/FastAPI process with migrations, authenticated login, CSRF, isolated
file-backed SQLite, a personal workspace and a real saved PyPSA project. The
frontend build came from the exact same source tree as the reviewed squash.
Test project and credential files were isolated under the workspace; personal
or production projects were not used.

The active model profile pointed to a loopback-only scripted OpenAI-compatible
SSE endpoint with no bearer authentication. The real provider wire, ordinary
stream route, turn loop, tool dispatch, confirmation, persistence and native
handlers ran. Five chat-stream requests used no public `script` frames. App
HTTP/SSE endpoints were not fulfilled with fake success responses. Browser
outbound non-local HTTP was blocked, provider keys were removed from the test
process and no paid model/media calls occurred. This is not vendor-model
intent/quality, microphone, speech, real-device or all-family evidence.

The independent numerical fixture was Bus B1 and Generator G1 with capacity
20 MW and initial marginal cost 10. Setup/reset used authenticated public APIs.
Assertions read the native generator endpoint after actions; the scripted
model's completion sentence was never the effect oracle.

## Observations

| Scenario | Result | Actual observation |
| --- | --- | --- |
| Access snapshot and native/external confirmation floors | PASS | Actual owner/project snapshot, Expert mode, standard personal rule, external write still requires user approval |
| Saved confirm-changes rule and denial | PASS | A real Expert bulk parameter write waits for the browser card; value stays 10 before approval and after denial |
| Owned browser approval | PASS | Native marginal cost changes from 10 to exactly 20 after explicit approval of the review; ordinary handler/read-back path exercised |
| Changed policy while approval waits | PASS | Save read-only, then click the old approval: requested 30 is not applied; native value remains 20 |
| Read-only write refusal and permitted read | PASS | New write to 40 is refused without a new approval card; native value stays 20; a real get_component turn remains usable |
| Pointer access to Check answers | **FAIL** | Chromium cannot click the checkbox: Upload a file intercepts the pointer. It reproduces with the Access review expanded and collapsed at the default desktop composer height |
| Keyboard checking and in-page summary | PASS | Keyboard focus/Space enables checking; companion shows the actual read-only rule and checking selection from the same mounted chat. Closing it performs no effect |
| Narrow/reduced-motion review | **FAIL** | At 320×850, the full page has horizontal overflow while the companion review is open. This run did not establish whether the background workbench or companion is the cause; compare before/after geometry during the repair |

No browser page errors were recorded. That does not erase the two interaction/
layout failures. No forced clicks were used. Keyboard success was recorded as
a separate fallback case, not substituted for pointer acceptance. The narrow
scenario changed viewport/reduced-motion settings; it was not a real touch
device measurement.

## Reproduction and retained evidence

Workspace root:
`/workspace/scratch/github-actions/chat-permissions-closeout-evidence/`.

- `e2e-server.py`: isolated real app and loopback model fixture.
- `e2e-probe.mjs`: actual login and native project/network setup.
- `e2e-browser.mjs`: the eight public-browser/native-value scenarios; returns
  nonzero when a scenario fails and retains the failed case in its report.
- `browser-e2e/e2e-results.json`, `browser.log`, filtered `model-calls.json` and
  screenshots including `desktop-confirmation.png`, `desktop-companion.png` and
  `failure.png`. The latest failure image shows the narrow viewport.

Credentials/cookie state and local app data are test-only private artifacts;
they are not committed, printed or included in this report. Test harness setup
was corrected for isolated storage, the actual login redirect, empty-composer
Send behavior and exact UI wording; no production guard/test was weakened.

These workspace runners are investigation artifacts, not a new CI-owned suite.
Promote the stable reproduction into the appropriate repository browser suite
with the LC-02 repair. Source/commands and current behavior must be revalidated
on its replacement head; do not label these failures as flaky or skipped.

## Next action

LC-02 owns the pointer/composer geometry and narrow-view investigation. Capture
actual bounding boxes/hit targets, retain a RED browser reproduction, repair the
layout, then require pointer, keyboard and actual coarse-pointer cases with
expanded/collapsed Access and saved resize preferences. Existing passing CI for
PR361 remains accurate source evidence; it did not cover these browser failures.

The broader [whole-application assessment](../../../.scratch/live-copilot/gap-assessment.md)
and [implementation plan](../plans/2026-10-18-whole-application-live-copilot.md)
cover generic tool/parameter workflows and result/voice gaps. No general
results/sensitivity walkthrough or continuous-speech readiness is established
by this permission journey.
