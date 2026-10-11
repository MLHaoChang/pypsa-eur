# Checked-answer pilot: activation and rollback

This guide covers the bounded user-facing pilot, not all-domain release or
independent engineering approval. The feature defaults to disabled. No global
or production deployment setting is changed by the implementation PR.

## Server setup

Set these in the backend process environment or its local `.env`, then restart
using the application's existing launch procedure:

```dotenv
PYPSA_GUI_ANSWER_ASSURANCE_MODE=pilot
PYPSA_GUI_ANSWER_ASSURANCE_PROFILES=generator-dispatch/1,generator-cost/1,network-linear-cost/1,storage-unit-dispatch/1,storage-unit-cost/1
PYPSA_GUI_ANSWER_ASSURANCE_EFFECTS=disabled
PYPSA_GUI_ANSWER_REVIEW_MODE=disabled
```

Keep only approved-for-your-pilot profile IDs in the allowlist. Omission permits
all five implemented profiles; an empty value rolls back calculation profiles.
Writes and qualitative model review have separate opt-ins and are unnecessary
for checked numerical answers. Existing keys, model profiles, authentication,
project access, confirmations and usage budgets still apply.

`GET /api/chat/reliability/capabilities` reports version 1, `mode: "pilot"` and
enabled profile IDs. The browser reads this through the ordinary authenticated
API client, refreshes availability and refuses malformed/unsupported responses.
It does not infer availability from provider/model names or old answer badges.

## Use in chat

1. Open a solved project and enable **Check answers** above the composer.
2. Ask for a supported scope, for example:
   `Give me the key results for storage unit Battery.`
3. For direct costs, specify the basis explicitly:
   `Show total cost for storage unit Battery; price year 2026; annual fixed cost fraction 0.5.`
4. Inspect **Requested results checked** or **Checked evidence and limitations**.
   A checked request can still be limited: unsupported quantities and missing
   dependencies remain unavailable. Export uses the existing checked JSON/ZIP
   links and revalidated facts.

The browser sends `assurance: {"version":1,"mode":"strict"}`. It withholds text,
thinking and raw frames before the server's checked-session acknowledgement,
then publishes/speaks only matching digest-approved text. Missing/downgraded
acknowledgement or a turn ending without valid approval produces a visible error.

Checking applies to typed/dictated, queued and Live sends through the same
dispatcher. Known checked retries, edits and software clarification choices
preserve checking even if it has been turned off for **new** turns. Historical
retry-policy hints survive an unavailable/expired answer; they cannot create
evidence, verified labels or permission to execute an operation. Interrupted
messages restored manually from the existing pending-turn banner still require
the user's current explicit choice; no generic task/interrupt recovery is added.
New chats, project changes and application reload reset the optional choice.
Strict server mode always requires checks and cannot be opted out in the UI.

## Rollback and failures

- Set the mode to `disabled` (or `shadow` for ordinary unapproved observation)
  and restart to stop new explicit checked turns. The API refuses them with
  `assurance_unavailable`; the browser does not retry as ordinary chat.
- Narrow/empty the profile allowlist to make affected calculations unavailable.
  This does not independently certify remaining profiles or globally disable
  static-input guards. A wholly rolled-back pilot calculation list cannot
  advertise available calculation checks in the chat control.
- If availability disappears while opted in, the browser retains that choice
  and refuses the next checked send. It preserves a typed draft and drops a
  refused queued request so recovery cannot replay it unexpectedly. Retry
  availability, or explicitly uncheck for a **new** ordinary pilot message.
- A failed capability fetch never means available checking. The control offers
  a manual availability retry and retains conservative previously required
  policy until it can read the current server configuration.

Always assess the current receipt. The checkbox expresses requested behavior;
it does not mean a result was verified. Charging purchases/revenues, Stores/Links,
NPV, arbitrary-language completeness, other domain adapters, independent review,
full browser/device coverage and measured release calibration remain pending.
See [contracts](harness-contracts.md), [implementation status](implementation-status.md)
and the [bounded acceptance plan](checked-opt-in-plan.md).

## Validation and reproduction

The [validation record](checked-opt-in-validation.json) lists the exact selected
backend suite and source hashes. The gate passes 1,045 cases / seven skips;
the fresh complete frontend passes 3,678 in 312 files, including 37 new pilot/API
cases. The focused 81-case UI gate and five pilot backend cases overlap those
larger suites. TypeScript/Vite build, inventory and touched-file Ruff pass.

From the frontend directory, `npm test -- --maxWorkers=2` runs the complete UI
suite, and `npm run build` builds it. With Playwright available to Node and local
Chromium installed, run:

```sh
CHROMIUM_BIN=/usr/bin/chromium node scripts/smoke-checked-answers.mjs
```

This isolated native-browser probe uses mocked HTTP API responses with the real
chat UI, fetch/SSE parser and browser digest implementation. It exercises keyboard
opt-in, pre-ack/private-text suppression, approved publication, HTTP rollback,
draft/choice preservation and explicit opt-out at a 380-pixel panel width. It
does not exercise a paid model or real backend authentication/evidence stores;
those backend numerical/auth paths are independently covered by pytest. Temporary
screenshots/JSON are executor-local; the script and commands are committed.
Broader browser/device/domain/calibration journeys remain pending.
