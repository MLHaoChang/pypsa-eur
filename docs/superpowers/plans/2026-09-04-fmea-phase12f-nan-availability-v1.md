# Phase 12f — what a missing availability hour means (plan v1)

**Status:** proposed. Supersedes the backlog item recorded as "the margin
derate NaN rule", whose premise §0 shows is false.

## 0. The item as recorded is wrong, and building it would make things worse

The shipped-code review of Phase 12c-pre recorded this open item:

> the margin's net-load *window* nets a NaN hour as 0, but its *derate* takes
> a pandas mean that skips it (measured: 0.45 against 0.225)

and the implied fix was to bring the derate onto the engines' rule — NaN
counts as availability 0. **That is measured to be the wrong direction.**

There is a third consumer nobody checked: the LP. `p_max_pu = [0.5, NaN, 1.0]`
on a 100 MW unit against a 95 MW load dispatches `[50, 95, 95]` — the NaN
hour is **unconstrained**, so PyPSA drops the bound and the unit runs at
nameplate. Verified on PyPSA 1.3.0 with HiGHS.

So one missing hour means three different things:

| consumer | a NaN hour is | derate on the fixture below |
|---|---|---|
| **the LP** — the plan actually built | **1.0**, unconstrained | **0.4750** |
| the reserve margin's derate (gross and net) | **skipped** from the mean | **0.4500** |
| the adequacy engines; the net-load *netting* | **0**, unavailable | **0.2250** |

Fixture: `profile = [0.9, NaN, 0.9, NaN]`, `q = 0.5`, window = all four hours.
The recorded 0.45 → 0.225 move is real — and it takes the margin from
**0.025 away from the LP to 0.25 away, ten times worse.** The margin's derate
feeds `Σ d·P ≥ (1+m)·peak`, a constraint the LP is then held to, so this is
not a reporting difference: it decides how much firm capacity gets built.

The item was recorded from two of the three rules. With the third in view the
question is not "which of our two rules should the derate use" but **"what
does a missing hour mean, and who is allowed to decide silently"**.

## 1. Two ways a NaN gets in, neither guarded

1. **The public write path does no validation at all.**
   `PUT /api/network/timeseries/{component}/{attribute}`
   (`routers/network.py:3154`) is `pd.DataFrame(data, index=idx, columns=cols)`
   straight into `ts_store[attribute]`. JSON `null` becomes `NaN` in a float64
   column — confirmed. `Infinity` is accepted by JSON too, and the 12c-pre
   review already had to defend the engines against `±inf` reaching them from
   this same route.
2. **Partial coverage, which is the commoner one.** Every consumer calls
   `.reindex(...)` against the snapshots or a peak window. A profile that does
   not cover them NaNs the rest. On the fixture above a two-hour profile over a
   four-hour horizon gives exactly the same three-way split. The known API
   defect that `PUT /timeseries` builds a plain `DatetimeIndex` while
   multi-period snapshots are a MultiIndex makes total non-coverage reachable,
   not just partial.

## 2. The resolution: refuse, do not guess

The codebase already answers this question for the neighbouring case, and the
comment states the rule: *"Split by EVIDENCE, not by absence"*
(`solver_service.py:3393`). A carrier with no derating evidence is not
credited at some default — it is **excluded and named** in `unpriceable`,
because "crediting it would mean defaulting its derate to 1.0"
(`solver_service.py:3442`). A missing availability hour is missing evidence of
exactly that kind.

So:

- **[F1] Reject non-finite values at the write boundary.** `PUT /timeseries`
  returns 422 naming the offending column and the first few row labels, as
  every other input bound in this codebase does. Nothing else can enter.
- **[F2] Name what is already there, at preflight.** A network can carry
  non-finite hours from an import or from a pre-12f upload, so a validation
  issue lists every `(component, attribute, column)` with non-finite hours
  inside the horizon. This is a warning, not a block: it describes data the
  user already has.
- **[F3] The margin refuses rather than guesses.** A generator or storage unit
  whose availability series carries a non-finite hour **inside the window being
  evaluated** joins `unpriceable` with its own reason, exactly as a unit with
  no outage evidence does. No derate, no number, a named refusal — instead of
  three surfaces each picking a different one of 1.0, skip and 0.
- **[F4] Partial coverage is the same refusal.** A profile that does not cover
  the evaluated window is missing evidence for the hours it omits. The
  refusal names coverage rather than "NaN", because that is what the user has
  to fix.
- **[F5] State the engines' rule where a user can see it.** The engines keep
  `NaN → 0` (`copt.py:502`): it is the conservative reading and changing it
  would move every shipped LOLE. But it is currently stated only in a
  docstring, so the `/copt` and `/mc` payloads carry the disclosure for any
  unit it applied to, the way every other engine assumption on these surfaces
  already does.

### Rejected alternatives, with the measurement that rejects each

- **Adopt the LP's rule (NaN = 1.0) everywhere.** It would make the engines
  measure the plan the LP built — the Phase 12c-0 principle — but it credits a
  hour we know nothing about at full nameplate, silently. It is the one thing
  `unpriceable` exists to prevent, and it is the most optimistic of the three.
- **Adopt NaN = 0 everywhere** (the item as recorded). Measured above: it
  takes the margin ten times further from the LP, and it reads a
  partial-coverage upload as "the unit is broken" rather than "we have no data
  for these hours".
- **Pick one rule in one shared function and disclose it.** Better than three,
  but it is still a silent guess on a number the LP is held to, and whichever
  constant it picks is wrong for one of the two readings above.

## 3. What moves for a user

Nothing at all on a network whose availability series are complete and finite
— which is every fixture in the suite and every network the QA plan builds.
The byte-identity anchor (18 fixtures × 2 modes) must stay green unchanged;
that is the gate, not a hope.

On a network that does carry non-finite or short series: a unit that was
silently credited at some derate is now **refused by name**, so a margin that
read `met` can read `unmet` with a reason. That is the point — it was met
against a number three surfaces disagreed about — but it is user-visible and
the panel copy must say which unit and why.

## 4. ★ tests, each with the broken variant it must fail against

- **F1a** a `null` in the PUT body is 422, naming the column. Bite: drop the check.
- **F1b** an `Infinity` likewise. Bite: check `isnan` instead of `isfinite`.
- **F1c** a finite body still writes. Bite: reject everything (the guard must
  not be a wall).
- **F2a** preflight names a column with a NaN hour inside the horizon, and is
  **silent** on one whose NaN sits outside it. Bite: drop the horizon
  restriction — the check then fires on every network with a longer stored
  series.
- **F3a** a unit with a NaN hour in the peak window is `unpriceable`, with its
  reason, and contributes **no** term to the margin. Bite: keep the skipna
  mean; the unit is credited 0.45 and the test sees a term.
- **F3b** the same unit with the NaN **outside** the window is priced normally.
  Bite: refuse on any NaN anywhere — the refusal must be about the window it
  is evaluated on.
- **F4a** a profile covering half the snapshots is refused for coverage, and
  the reason says coverage rather than NaN. Bite: report it as a NaN refusal.
- **F5a** the `/copt` payload discloses a unit whose NaN hours the engines
  zeroed. Bite: drop the disclosure.
- **F6 (the anchor)** every existing fixture with complete finite series
  produces byte-identical derates, terms and payloads. Bite: apply the refusal
  unconditionally.

## 5. Out of scope, stated

- Changing the engines' `NaN → 0` rule. It is conservative, it is now
  disclosed, and moving it would move every shipped LOLE for no measured gain.
- Fixing `PUT /timeseries`' plain-`DatetimeIndex`-versus-MultiIndex defect.
  It is a recorded, separate bug; F4 makes its consequence visible rather than
  silent, which is this phase's job.
- The static-CF per-asset flag and the `validate` route's TOCTOU: still the
  other two backlog items.
