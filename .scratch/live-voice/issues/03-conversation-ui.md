# Continuous voice UX and interruption

Status: ready-for-agent
Progress: Presence-led Live grid node shipped; no continuous media, real-device gates or speech transport; blocked on issue 01
Type: task
Blocked by: 02, 08, 09 (typed UI); 01, 11 (media/monitoring acceptance)

Implement steps 2 and 4's UI in [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
The floating Compose/Dictate/Live presence is [issue 12](12-companion-launcher.md)
and can land before media. This issue owns continuous conversation. Live stays
on the page the user is already using: the companion lights up, a short caption
sits beside it, and the shared harness navigates, creates, and announces
downloads there. Confirmation is a card beside the companion. Live does not
open the console. The console remains the typed composer, uploads, history,
and reviewed dictation.
Begin the persistent assistant dock redesign using typed chat after issue 02;
media-dependent acceptance requires issue 01. Add project/run context, inline
evidence/progress/download/confirmation cards and natural request-driven navigation.
Add independent live-mode lifecycle, captions, input/output audio, mute/end,
playback stop and distinct task cancellation. Retain reviewed dictation and typed
fallback. Cover stale grants, echo/autoplay, navigation and validated project rebind.
Cover all mapped panels/result tabs/editors/filters/selections, cursor monitoring
and versioned results refresh. Newer user navigation wins over notifications.
Keep durable current-view and discussed-result context distinct.

Done when Chromium integration and real-device gates pass; microphone/speaker
resources close on every exit. See [spec](../spec.md).

## Comments

2026-11-17: plan in [03-plan.md](03-plan.md), slices 03a–03e. UX 24 GO (Q1 a, Q2 a,
Q3 a, conditions in the plan). GK B 64 GO for 03a (refs.current written at
admission, own PR) with conditions:
- L1: per-value allow-list (panel, results_tab, mode closed enums; section enum or
  bounded id; run_id/asset_id id pattern with cap; window typed and bounded); a
  failing value is dropped, never stored; `at` is server time. Wrong type,
  oversize, free text and bool tests per field.
- L2: no new refusals; stale generation writes nothing and the turn answers as on
  master (byte-equal test); no thread or no ui_context keeps the old record.
- L3: name the admission lock and its order against `_LOCK`; no write under a
  session or provider lock; concurrent admission + resume/bump never tears.
- L4: refs.discussed byte-equal before and after in every write test.
- L5: delivery `expires_at` = `uploaded_at + DELIVERY_TTL_SECONDS` for
  `agent_export` only, null for other kinds; boundary tests at TTL (served) and
  TTL + 1 s (410), expected values from the constant; inventory, route parity
  and tool-result snapshots at the trial merge.
- L6: non-owner, other-project (and viewer, if #1622 merged) refusals byte-equal.
- L7: one targeted mutant per guard, listed in the PR body with killing tests.
The loop hook waits for #1550 (it changes `harness/loop.py`).
