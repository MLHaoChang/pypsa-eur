<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Codex handoff — whole-application assistant / live voice

## 2026-10-14 planning continuation

For the owner's accepted Dots comparison, start with the new [companion specification](../chat-companion/spec.md), [implementation plan](../../docs/superpowers/plans/2026-10-14-chat-companion-implementation.md) and [pending acceptance matrix](../chat-companion/test-plan.md). These are pinned to master `4ac218cc98a1437950dd20c3f46233efac6cc159`; rebaseline before implementation. First slices: CP-01 truthful mode/state, then CP-02 readable source-bound outcomes.

PRs #185 and #188 are already merged: bounded prerequisites and one network case are implemented. Their merge commits are `301ba9bb6afa37bc0083c7c407e5f5d118719701` and `5be393bcd2cd2055928025fcd838f39d5d02c72f`. Do not repeat them. Two-case comparison/reconciliation and the other new companion capabilities remain planned. The older baseline/status prose below is historical; this planning continuation creates no application implementation or new test evidence. Hosted/device gates, independent domain review and real-use calibration remain pending.

## Earlier handoff

Implementation continuation: [CP-01 progress/evidence](../chat-companion/issues/01-truthful-state.md)
uses newer master `f2b15c5b2caae8fc1259396158355007a670f531`. Its local UI
implementation does not enable continuous media or complete live/domain gates.

**Audience:** next agent (Codex or Cursor) starting from **master**.  
**Date:** 2026-10-13.  
**Master tip at handoff write:** `8a4927f9d` (includes #129 Moonshot alias, #126 run refs UI, #127 experiment persist, #130 resume discussed-run rehydration).

Do **not** claim whole-application coverage or live voice done. Issue 12 (companion shell) is done; media, budgets, release journeys, and full experiment orchestration are not.

---

## Read first (in order)

1. This file.
2. [spec.md](spec.md) — Companion launcher + release gates.
3. [pypsa-gui/backend/harness/README.md](../../pypsa-gui/backend/harness/README.md) — chat harness contract.
4. [AGENTS.md](../../AGENTS.md) — Cursor Cloud secrets (`MOONSHOT_API_KEY` / `KIMI_KPI_KEY` alias); no credential commits.
5. Issue files under [issues/](issues/) for the slice you pick.
6. Historical log: [session-handoff.md](session-handoff.md) (append-only; prefer this file for “what next”).

Plans: `docs/superpowers/plans/2026-10-10-live-voice-conversation.md`, companion plan `docs/superpowers/plans/2026-10-10-assistant-companion.md`.

Continuation plan: [remaining assistant implementation](../../docs/superpowers/plans/2026-10-13-assistant-remaining-work.md).
The first bounded prerequisite slice adds `prepare_experiment`, typed caps and
exact input preflight digests while retaining `execution_available=false`.
See [its contract](experiment-prerequisites.md). Case advancement remains the next
scope; this metadata tranche does not complete issue 10 or external release gates.

The continuation adds [one network case](experiment-case-advancement.md) through
ordinary confirmed sensitivity/queue calls and durable no-replay receipts.
Next: two-case compatible comparisons, explicit uncertain-effect reconciliation,
then other domain recipes. This remains partial issue 10 coverage.

---

## Inventory / env snapshot

| Item | Value |
| --- | --- |
| Tools | **244** (inventory + GUI nav schemas match; behavioral/parameter coverage still pending) |
| Result tabs / panels | 14 tabs, 18 panels |
| Full-coverage claim | **false** — see `capability-matrix.json` `full_coverage` and issue 08 |
| Dev UI | API `:8000`, Vite `:5173`; login `admin@example.com` + password file `~/.local/share/PyPSA Studio/dev-admin-password` |
| Moonshot / Kimi | Preset `moonshot` → `MOONSHOT_API_KEY`; bootstrap aliases `KIMI_KPI_KEY` if Moonshot unset. Live closed-console proof already done once (see below). |
| LiveKit / STT / TTS | **Not** provisioned; block 01/03/04 live media gates |

Branch naming for this repo’s Cursor agents: `cursor/<slug>-a604`. Prefer `pypsa-gui/` only.

---

## Status by issue (01–12)

| # | Issue | Status | Landed on master (summary) | Remaining |
| --- | --- | --- | --- | --- |
| 12 | Companion launcher | **done** | Floating Compose / Dictate / Live; closed-console harness path; CompanionOutcome | Polish only; media belongs to 01/03 |
| 08 | Application coverage | claimed | Nav enums, inventory CLI, control matrix (165 controls / 14 families), full-coverage limitation block (`claimed: false`) | Behavioral fixtures + journey pass → issue **05**; do not flip coverage claim without evidence |
| 09 | Durable conversation | claimed | Parent thread, HTTP CAS, rebind, worker pin, B-C1 journey, **resume_episode hydrates `discussed_refs`** (#130) | LiveKit/deployment smoke; multi-worker stays off |
| 02 | Harness bridge | claimed | Utterance admission, `/stream` gates, Live busy→queue, auto-drain, Live open→durable rebind | LiveKit deployment smoke; no second catalogue |
| 11 | Nonblocking monitoring | claimed | Foreground/detached progress, observer SSE, `fg:` / `ad:` / `rpt:` wait adapters, D-C1 multi-family fakes | GridSpine/solve-queue in same matrix; D-I2 cross-family recovery; real-worker multi-family |
| 06 | Run evidence + context | ready-for-agent | Publish (fg/queue/study/report), list/get/compare, discuss + `open_run_result`, export, **three-run acceptance**, resume rehydration | Optional: more ACL/restart edge cases; keep “never restore historical inputs” |
| 07 | Loadflow summary | claimed | `get_loadflow_summary` over current results; historical `run_id` typed unavailable until loadflow section | Typed UI: open loadflow tab, highlight/scroll, ACK; shared client/server metric source |
| 10 | Cross-domain experiments | ready-for-agent | Typed planning/preflight and one network case via ordinary confirmation/queue; durable no-replay receipt, bounded waits, cancel and owned evidence/export path | Two/multiple-case compatible comparisons; explicit uncertain-effect retry reconciliation; commercial/reliability/GridSpine adapters |
| 01 | Transport | ready-for-agent | F-U1 mocked STT/TTS contracts (`harness/providers/live_media.py`) | Real vendor adapters, WebRTC/LiveKit, token routes, budgeted live probe |
| 03 | Conversation UI | ready-for-agent | Companion shell (via 12); typed closed Live path proven with Moonshot | Continuous media UX, mute/end, audio lifecycle, Chromium/device gates (needs 01) |
| 04 | Budgets / resume | ready-for-agent | Parent budget primitives exist on durable thread | Separate LiveKit/STT/TTS/model units; measured latency; speech→typed fallback without replaying writes |
| 05 | Workflow validation | ready-for-agent | Limitation block documents pending journeys | Feature-flag release criteria; family journeys; real-device / hosted gates |

Older “Still open” bullets inside some issue comment sections are **stale** relative to later merges (especially 02/06/11). Prefer the table above and the latest dated comment at the bottom of each issue file.

---

## Proven on this environment (do not re-prove unless regressing)

1. **Moonshot probe:** `POST /api/chat/settings/llm/profiles/moonshot-probe/test` → `verdict: ok`, models include `kimi-k3`.
2. **Closed-console Live:** companion Live composer (dock collapsed) with Moonshot active → assistant sentence on CompanionOutcome. Manual click on Live can miss hover CSS; `document.querySelector('[data-testid=companion-live]').click()` opens the composer reliably.
3. **Acceptance pytest:** `tests/test_run_evidence_acceptance.py` + discuss/export suites.

Do not paste API keys into logs, scratch, or PRs.

---

## Recommended next slices (pick one per PR)

### A — No credentials (preferred next)

1. **Issue 10 two-case continuation** — Extend the single network-case receipt path to compatible comparisons and explicit partial outcomes. Reconcile uncertain effects before any retry; retain ordinary confirmation, locks and budgets. Keep `executed` honest; no hidden agent loop. Tests first (TDD).
2. **Issue 03 mocked scaffolding** — Presence/caption/confirmation/download cards without real mic; Chromium unit/integration only. Does not unblock media acceptance.
3. **Issue 07 loadflow UI** — `results_tab='loadflow'` navigation + highlight/ACK wired to `get_loadflow_summary` window/source.
4. **Issue 11 residual** — Put solve-queue/GridSpine into D-C1-style matrix; D-I2 cursor recovery across families.

### B — Needs Moonshot only (key often already present)

- Companion Live multi-turn with tools (navigate + confirmation card) while dock stays closed.
- Do not burn paid LiveKit/STT/TTS.

### C — Blocked on external credentials / deployment

- Issue **01** LiveKit Build + real STT/TTS + token routes.
- Issue **04** vendor billing units and measured speech latency.
- Issue **05** / **09** hosted deployment smoke and feature-flag release.
- Issue **03** real-device mic/speaker gates.

---

## How to branch and verify

```bash
git fetch origin master && git checkout master && git pull origin master
git checkout -b cursor/<slice-slug>-a604

# Inventory contract
python -S pypsa-gui/backend/smoke/assistant_inventory.py --check
python -S pypsa-gui/backend/smoke/assistant_control_matrix.py --check

# Focused pytest (examples)
cd pypsa-gui/backend && PYTHONPATH=. python -m pytest \
  tests/test_run_evidence_acceptance.py \
  tests/test_experiments_persist.py \
  tests/test_discuss_project_run.py \
  tests/test_export_run_evidence.py -q

# Frontend companion / Live
cd pypsa-gui/frontend && npm test -- --run AssistantDock CompanionLauncher CompanionOutcome
```

After catalogue/toolset changes: `python pypsa-gui/backend/smoke/assistant_inventory.py --write`, review diff, commit.

---

## Out of scope for this handoff

- Open IC-engine / Expert-sidebar / unrelated GUI fix PRs on the remote (e.g. #170–#178) — do not mix into live-voice slices.
- `gui_streamlit/` (frozen).
- Claiming issue 08/05 complete without journey evidence.

---

## Append after your session

1. Update the relevant `issues/NN-*.md` Comments + Status if triage changes.
2. Append a dated bullet to [session-handoff.md](session-handoff.md).
3. Refresh the “Status by issue” table in **this** file if a slice closes or a blocker moves.
