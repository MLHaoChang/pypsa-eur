# Handover: companion on master, Kimi key in the next session

Date: 2026-10-11. Audience: the cloud agent that boots with `KIMI_KPI_KEY` stored on the `mlhaochang/pypsa-eur` environment. This session proved the variable was absent here. It did not call Moonshot.

## Done

Master is `b8bc079fd` (merge of pull request 113). The floating companion and the typed harness bridge are on that commit.

- Live stays on the page. Compose and Dictate open the existing console. Dictate stays reviewed transcription.
- When the active profile's `chat_ready` is true, a live sentence is `companion:live-utterance` → `ChatPanel.dispatchSend` with `{ fromCard: true }`. That is the same stream and the same 220-tool catalogue as the console.
- The confirmation card, the latest assistant sentence, and one upload-blob download render beside the companion while the console is closed. `ChatPanel` stays mounted.
- With no key, Live only walks the results tabs and opens the project list. Any other sentence captions: `Add an API key for the assistant first. Tab changes and opening a project still work without one.`
- Frontend suite after the bridge: 304 files, 3567 tests passed. Four older backend `gui-tests` failures stay as they are: `apply_project_changes`, `import_uploaded_network`, `invalid_navigation`, `task_checkpoint_failed`.

Plan that shipped: `docs/superpowers/plans/2026-10-11-companion-harness-bridge.md`. Coverage contract: `.scratch/live-voice/application-coverage.md` (local issue tracker; the same rules are in the live-voice plan).

## First job in the session that has the key

`KIMI_KPI_KEY` is an environment Runtime Secret. The Kimi preset does not read that name.

| Fact | Value |
| --- | --- |
| Profile id | `moonshot` |
| Label | Moonshot AI (Kimi) |
| Wire | `openai` |
| Base URL | `https://api.moonshot.ai/v1` |
| Env var the app reads | `MOONSHOT_API_KEY` |
| Suggested models | `kimi-k3`, `kimi-k2.6`, `kimi-k2.5` |
| Source | `pypsa-gui/backend/presets.json` |

Runtime secrets are injected when an agent starts. A running agent keeps the environment it booted with. The value is redacted from tool output, transcripts, and commits. Report presence, length, HTTP status, and `chat_ready`. Leave the secret out of chat, logs, commits, and screenshots.

1. Confirm the boot actually received the secret. In Python, print only `present`, `nonempty`, and `length` for `KIMI_KPI_KEY`. If it is absent, stop. The environment page was still **Ready to save** when this handover was written. Save, then start another agent. This boot will not gain the secret later.
2. Before the API process starts, copy it once:

```bash
if [ -n "${KIMI_KPI_KEY:-}" ] && [ -z "${MOONSHOT_API_KEY:-}" ]; then
  export MOONSHOT_API_KEY="$KIMI_KPI_KEY"
fi
```

Restart uvicorn from that same shell so the process inherits `MOONSHOT_API_KEY`. A copy made only inside an agent command that cannot see runtime secrets does nothing.
3. Check the key with `GET https://api.moonshot.ai/v1/models` and `Authorization: Bearer` from `MOONSHOT_API_KEY`. Success is HTTP 200 and a non-empty model list. Print the status code and the model ids. On 401, stop and say the stored secret was rejected. Make no second call against a rejected key.
4. Activate the `moonshot` profile in the app and read `GET /api/chat/profiles` (auth required). Done when `moonshot.chat_ready` is true. `/health` reports `chat_ready` for the active profile only.
5. One cheap live sentence, with the console closed: ask it to list projects. Done when the companion caption says it was sent, the assistant reply appears in the companion outcome, and no write tool ran. A results-tab walk or opening the project list does not count; those run with no key.

Writes still wait on the confirmation card. A sentence sent while streaming, while a confirmation is pending, or while `projectMismatch` is set is refused with `The assistant is busy. Nothing new was sent.`

## Still open after that proof

The catalogue already contains creation, workflow, solve, all 14 result tabs, import, export, and analysis. Offering those names is tested. A live journey through them is not. The coverage contract forbids a whole-application claim until each required interaction is mapped.

| Issue | What remains |
| --- | --- |
| 08 | Editable-control and parameter matrix, eligibility-aware UI targets, AR mapped to the real module name, one journey assigned per family. Adequacy and FMEA tab ids are already in the schema. |
| 05 | One real journey per family, with values checked against the project. A catalogue listing does not qualify. |
| 11 | `run_simulation` and `run_ac_pf_stage` hold the chat turn until the job finishes. |
| 02, 09 | Durable thread across navigation, rebind, and restart. The typed live path is the piece that exists. |
| 01, 03 | LiveKit, speech-to-text, speech synthesis. Out of scope until the typed path has the key proof above. |

Project creation is `create_project_from_template`. OpenAI can be offered 128 of the 220 tools on one request; `find_capabilities` and `use_toolset` reach the rest. The companion download chip accepts only `/api/projects/<id>/uploads/<16 hex>/blob`.

## How to run the app

- API: `python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload` from `pypsa-gui/backend`, via `pixi run -e dev`. Workspace pixi env is `default` / `dev`.
- UI: Vite on `127.0.0.1:5173`. Workbench: `http://127.0.0.1:5173/app`.
- Login email `admin@example.com`. Password file: `~/.local/share/PyPSA Studio/dev-admin-password`. Leave the password out of chat and logs.
- In dev, the results walk advances only when `GET /__pace?ms=` returns the body `paced:<ms>`. `KEY_RESULT_STEP_MS` is 1800. `/__pace` is a static pass-through in `pypsa-gui/frontend/vite.auth-gate.ts`.
- Solver status is `GET /api/simulation/status`. Idle is `{"running":false,"status":"idle"}`.

## Leave alone

- Paid calls beyond the models check and one short read, until that read is shown.
- New tools, catalogue edits, a second planner, or a voice-only tool list.
- LiveKit, Deepgram, and TTS.
- The four pre-existing `gui-tests` failures.
- Printing `KIMI_KPI_KEY`, `MOONSHOT_API_KEY`, or the admin password.
- Hard-refreshing `/app` while a solve might be running. Browser timers in this VM collapse; the Vite `/__pace` middleware does not.
- Environment setup. Snapshot `snapshot-20261010-fabfd3e2-f044-4398-ac6b-c6078923d8a9`. Draft build `bld-20261010-095213d4-36dc-4018-a6ba-8f178406c587` succeeded. Environment id `ae0a778e-c4d9-11f1-ac31-5e2d0494121f`. The owner still clicks **Save** on the environment page.
