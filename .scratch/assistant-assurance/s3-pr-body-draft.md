Part of `.scratch/assistant-assurance/issues/10-metric-calculation-and-validity.md`. This is slice S3 of the 09+10 plan: GK B 64 plan GO, condition G3; UX 25 leads (G9). It is stacked on S1b.

**Merge order (UX 25 condition):** ticket 20 (`.scratch/assistant-assurance/issues/20-clarification-answers-refused-binding-generation.md`) lands before this PR, or this PR waits for it. Today, answering any checked-answer clarification card in the app fails. The client increases `binding_generation` on every send, and the server requires the same value as the clarification turn. That bug is not new, but this PR adds a new card.

## Changes proposed in this Pull Request

- **`services/results/objective_decomposition.objective_bridge()`**: a public form of the bridge.
  - Identity: `objective = total − non-extendable fixed + period-weighting adjustment − reserve revenue + residual`.
  - **Bug fix:** `_bridge` derived the period-weighting term from `cost_breakdown["total"]`, so on a single-period network any error in the total went into that term and the residual stayed 0. With a 10 EUR over-report, the residual read 0 and the adjustment read −10.
  - The wrapper now builds the term from the cost parts, using a new additive `_bridge` key, `reported_basis_eur`. The error now lands in the residual.
  - `/results/objective_decomposition` output is unchanged.
  - `RESIDUAL_TOLERANCE = 1e-6`, relative to max(1, |objective|), so never below 1e-6 EUR (`residual_tolerance()`).
- **Penalty row.** The reported total includes the cost of slack generators (VOLL, DSR and the other slack carriers). Their share is read from `by_carrier`. A slack generator under an ordinary carrier makes the row unavailable (`not_separable`), never 0.
- **`harness/objective_bridge_calculations.py`, `objective-bridge/1`**: network only, opt-in, whole run; a partial window is refused.
  - Rows, in bridge order: Solver objective, Total system cost, its penalty share, non-extendable fixed cost, period-weighting adjustment, reserve revenue, and the residual last.
  - The objective is read only after the post-solve rescale. If a scale is still applied, the row reads `still_scaled`.
  - A rolling-horizon run answers no row (`last_window_only`); a myopic run would read `last_period_only`.
  - Registered in `calculations.py` as the coordinator allowed (G7, option a).
- **Chat card (UX 25 Q1a/Q2a).** "What is the objective?" or "objective vs cost" gets one card with exactly three buttons: "Solver objective", "Total declared-basis cost", "Objective-to-cost reconciliation". A bare "total cost" keeps today's route.
  - **The two buttons name two different quantities.** "Total declared-basis cost" keeps its `network-linear-cost/1` route. The bridge's "Total system cost" row is the Results panel's number and wording.
- **Rendering (UX 25 interim).** The answer uses the shared checked-answer renderer, so money prints as EUR and an unanswered row as `unavailable (<code>)`.
  - The codes are plain words, and each one gets its own "Not available: …" note line in the same answer.
  - The match line ("Matches within … EUR." / "Does not match: the residual is …") appears only when the residual row is shown.
  - Disclosure: "The solver objective includes penalty and slack terms and period weightings, so it is not the total system cost."
- **L6 / AA-A04-REVIEW-1:** the contract JSON, the scope Literal, the recovery `pending` Literal and the frontend `checkedAnswers.ts` list all change, so GK B needs a contract re-review.
- **Tickets.**
  - **19:** € and "Not available: <reason>" wording for all checked answers, with no raw reason code in the text. It also lists the shared-renderer issues seen in the shots. It gets its own UX review.
  - **20:** the `binding_generation` bug above.

### Tests

- `tests/test_objective_bridge_assurance.py`. Expected values are hand-worked: 240 EUR dispatch + 1,200 EUR shedding = 1,440 EUR; the fixed charge is 10 × 8 × 6/8,760; the total is rounded to 5 decimals. It covers:
  - objective-scale reversal under user scale 2 and auto, through `run_simulation`;
  - a scale left unreverted;
  - the tolerance boundary, and one ULP above it;
  - an injected wrong total, which shows as a residual (G4 style);
  - unidentifiable penalties;
  - rolling and myopic runs;
  - windows;
  - identity;
  - an AC power-flow result present;
  - the exact disclosure and row order.
- `tests/assurance_acceptance/test_p2_objective_clarification.py`:
  - the card's labels, pinned by exact list equality;
  - each choice's route, end to end;
  - a bare "total cost" unchanged;
  - an answer typed while another card is open is not taken.
- Generic acceptance: `fact_profiles` driver on the `shed` fixture; registry and contract tests.
- `prepush.sh` passes: ruff, tsc, contract pytest, route parity, control matrix, vitest, mapped backend tests, repr guard.

### Mutants (G8)

`scripts/coord/mutants.py` was run against the S1b head, so it covers this PR's lines only:

| File | Mutants | Killed |
|---|---|---|
| `objective_bridge_calculations.py` | 60 | 59 |
| `objective_decomposition.py` | 41 | 38 |
| `assurance_conversation.py` | 25 | 23 |

Equivalent survivors:
- `:188` the match line's `'objective_eur' in available`: the residual exists only when the objective does.
- `objective_decomposition.py:308` `isinstance(rows, list)` and `:359` `total is None`: the `except` that follows gives the same `None`.
- `:137` `years *`: years is 1 on a single-period network, and the profile refuses multi-period.
- `assurance_conversation.py:219` `if asked is not None`: `_needs` has already recorded the card on the turn, and no later grammar matches these messages.
- `:163` `'retain': False`: this is `RequestScope`'s default.

### Shots (UX 25)

Five cases, each in light and dark: the card, matches, does not match (+10 EUR injected), penalty not separable, and rolling horizon.
- No model key exists in the build container. A scripted model drafted the ledger with the server's own fact values in the real backend; each run was solved through "Run LOPF".
- **The shot driver keeps the conversation's first `binding_generation`.** Without that, the card answer is refused in the app (ticket 20).
- The myopic sentence has a test but no real-run shot: myopic runs are multi-period, and every bounded profile refuses those.

**Journey fit:** chat only, in Guided and Expert alike. No Results card, chart or Guided step changes. The link to Results → Objective decomposition is deferred to a later PR, through the mode-aware opener.

**Skipped:**
- € wording (ticket 19).
- The Results link.
- `adequacy/report.py:509-515`, which falls back to a total system cost of 0.0 when the objective is not finite (noted for S4).
- Multi-period bridging in the checked profile.

**Risk:** until ticket 20 lands, the new card is a dead end in the app.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated: written by Claude Code.)

Session: session_01G7qCuni6Hzy1S3SLGXmQAK

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01G7qCuni6Hzy1S3SLGXmQAK
