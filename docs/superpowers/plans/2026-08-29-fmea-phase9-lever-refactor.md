# Phase 9 — the margin loop (plan, v2 post-review)

**v1 proposed the wrong shape and got one of its own premises backwards.** The
review's verdict — *"not as scoped"* — is accepted in full, and v2 is a
different design, not a patched one. What changed:

- **v1's §1 row 3 was INVERTED.** I wrote *"with no ENS cap set, `cap_mwh` is
  None and the test never fires."* It is `0.0`, not `None`: on a margin-only
  run `targets={}`, the per-period loop never executes, and `SystemTarget.
  cap_mwh` is emitted as its initialised `0.0`. So `0.0 < ENERGY_FLOOR_MWH`
  fires on the **first miss** and every margin run would end `unreachable`
  after one solve — indistinguishable in the payload from the real thing. The
  break is not "never fires", it is "always fires, immediately".
- **The generalised `Lever` does not earn its keep.** Of the eight breaks
  between today's code and a working margin loop, only three live in the
  controller's comparison operators. The rest are report, route, validator and
  frontend — a shared dataclass merely relocates them, while adding six members
  (`miss_is_final`, `sort_sign`, `midpoint`, `is_final_refusal`, a
  total-over-missing-result `stricter`, a new row field) that must all be
  regression-proved against a working Phase-7 suite. v1 named that risk itself
  ("refactors go wrong silently") and then took it anyway.

## 1. The shape: make the margin LOOK like a cap

`POST /results/margin_loop`, a second route, reusing **`run_coupling_loop`
unchanged** via a monotone substitution:

```
x = 1 / (1 + m)        m ∈ [0, ∞)  ⇒  x ∈ (0, 1]
```

Smaller `x` is a larger margin is stricter — which is exactly the ordering the
controller already assumes. Verified line by line against `coupling.py`:

| controller site | under `x` |
|---|---|
| `_tighten`: multiplicative shrink toward 0 | correct — and `m = 0` is `x = 1`, a legitimate loose end with no clamp problem |
| `assert e > 0` | correct by construction: `x > 0` for all finite `m` |
| `mid = sqrt(met·miss)` | **valid again** — both endpoints strictly positive. This is v1's hardest unseen problem (an arithmetic midpoint would have been needed, and `sqrt(0·miss) = 0` would have collapsed the bracket at the exact place it matters) |
| `if not miss > met·(1+1e-9)` | correct: the miss is the looser (larger) `x` |
| tie-break `key=(cost, -x)` | correct: prefers the larger `x`, i.e. the **smallest margin** among equal-cost met plans — the cheapest certified standard |
| `cap_mwh < ENERGY_FLOOR_MWH` | the new route's `solve_at` returns `cap_mwh=None`, which makes the test a genuine no-op (this is what v1 wrongly believed was already true) |

Cost: one substitution and a per-route payload shape. Benefit: `coupling.py`
is **not touched**, so the Phase-7 suite is a regression oracle rather than a
risk. If a unified controller is ever wanted, build it after this works, from
evidence about which hooks are actually needed rather than from a re-read.

**`EPS_FLOOR_PERMYRIAD` as an `x`-floor** is `x ≥ 0.01`, i.e. `m ≤ 99` — far
above the schema's `le=5`, so the route's own limit binds first and the
backstop never distorts the search.

## 2. What the route must do that the controller cannot

### 2.1 Refuse up front, in three cases the loop would otherwise burn solves on

`reserve_margin_facts(n, cfg)` is explicitly preflight-callable (*"Nothing in
this function touches `n.model`"*), so all three cost zero solves:

- **Unreachable margin** [B4]. An out-of-reach margin surfaces as
  `validation_failed`, **not** `infeasible` — `_is_infeasible` matches neither,
  so the controller treats it as a transient failure, keeps stepping, and ends
  `budget_exhausted` advising *"raise max_solves"*, which can never work. The
  route computes the ceiling itself (below) and refuses before starting.
- **Unpriceable assets** [S5]. `reserve_margin_unpriceable_assets` is a
  blocking error, but the loop's own gate only needs *one* priceable unit — so
  such a network passes the 422 and then fails **every** iterate identically.
  Reuse the validator's sentence verbatim.
- **`rolling`** only [S4]. The margin's validator refuses `rolling` and
  downgrades `myopic` to a warning, with a stated reason (each myopic window is
  one period, which is the peak the standard is defined against). The cap
  loop's blanket refusal of both would deny a supported configuration.
  **And the cap loop's VoLL requirement does not apply**: the margin is a
  constraint, not a price, so a margin loop on a VoLL-free network is well
  defined. (The MC still needs no VoLL either.)

### 2.2 The ceiling is `min` over periods, not `max` [B3, corrected]

The constraint is installed **per period**. A margin is achievable iff
`max_achievable_P ≥ (1+m)·peak_P` for **every** P, so the binding period is the
one that fails first:

```
m_max = min over P of (max_achievable_P / peak_P) − 1     (non-finite ⇒ +∞)
```

v1 said "max over periods, presumably" — exactly backwards; `max` lets the loop
search past a margin one period already makes impossible. And `max_achievable`
is **`inf` on the ordinary network** (PyPSA's `p_nom_max` default), where the
sanitizer nulls it — so the ceiling degrades to the schema's `le=5`, and any
acceptance test that exercises the ceiling needs a fixture with a **finite**
`p_nom_max`. v1's A3 could not have failed.

### 2.3 The informed step must strictly exceed the tight margin [S3]

`firm_mw / peak_mw − 1` is the smallest margin at which the incumbent plan is
*tight* — at exactly that value the plan is feasible, unchanged, same hash,
same LOLE, and flagged `binding` while nothing moved. It is the smallest tight
margin, not the smallest plan-changing one, so the step must strictly exceed
it. Aggregate by `min` over periods, for the same reason as §2.2.

Since `_row` discards `res["report"]`, the route computes this in its own
`solve_at` closure (it holds the report there) and returns it through the
substitution, rather than asking the controller to carry a new field.

## 3. The `binding` misdiagnosis is a LIVE bug, and is fixed first [B2]

`report.binding` is computed purely from ENS caps, so it is `"voll"` on every
margin run. Two consequences, one of which is shipped today:

- **Today, on the cap loop:** a user with a margin set runs the cap loop; the
  margin shapes the plan, the cap never binds, and `_verdict_copy` emits
  `NEVER_BOUND_COPY_V1` — *"what would move this number is firm capacity … a
  planning reserve margin"* — to someone who **already has one**. The
  diagnosis is not wrong about the cap, but its advice is stale.
- **On the margin loop:** the same copy would recommend the lever in use.

Fix, and it is small: the reserve-margin block already publishes a per-period
`binding` flag. `_verdict_copy` consults it, and when a margin was in force the
copy says so and names the margin's own binding state instead of recommending
it. This lands as its own commit **before** the loop, because it is a
correctness fix to shipped behaviour, not Phase 9 scope.

## 4. Frontend: a discriminator, no nullable alias [B5]

v1's additive alias **crashes the panel**: `compact()` is typed `number`, and
`isFinite(null)` is `true` in JS, so `null.toPrecision(2)` throws inside
`rows.map` and takes the whole panel down. Omitting the key instead leaves an
`ε ‱` column of em-dashes.

And v1 missed a second hard-coded site: `restoreSentence` writes
`ens_cap_permyriad = …` in **both** branches, rendered unconditionally — so on
a margin run the UI would tell the user to set the wrong config field
regardless of what the backend verdict says.

So: the payload carries `lever` (`"ens_cap"` | `"reserve_margin"`),
`lever_value`, `lever_label`, `lever_unit`; the panel drives its column header,
badge suffix (`‱` vs `%`) and `restoreSentence`'s field name off them. No
alias. Same session, one panel, three call sites.

## 5. Acceptance (self-calibrated — Phase 8's lesson, kept)

★ **A1 — the margin loop reaches `met` where the cap loop reports
`unreachable`, on the same network.** The phase's whole claim, on the fixture
where both prior phases already measured their halves.
★ **A2 — the informed step does not creep**: a non-binding start reaches a
binding margin in one solve.
★ **A3 — the ceiling refuses without spending solves**, on a fixture with a
**finite** `p_nom_max` (v1's omission), reporting the ceiling by name.
★ **A4 — `coupling.py` is untouched and the Phase-7 suite passes verbatim.**
Under this design that is a `git diff` assertion, not a test-suite hope.
★ **A5 — the substitution is monotone**: a property test over the mapping, so
the one piece of new math is pinned.

## 6. Non-goals

- A generalised `Lever` / unified controller (§1 — revisit from evidence).
- Auto-switching levers; the loop recommends, never acts.
- Tuning both standards jointly.
- ELCC-weighted derating; per-zone margins.

## 7. Open decisions, answered by the review

1. `1/(1+m)` over `limit − m`: the latter needs a finite limit, and the limit
   is `inf` on the ordinary network.
2. No alias — remove and discriminate [B5].
3. `min` over periods, not `max` [B3/S3].
4. Refuse unpriceable assets up front [S5].
5. A margin run leaves a user-set ENS cap untouched (`solve_at` and
   `_restore_closing` both build from `base_cfg`), so the certified plan met
   **both** standards and the verdict must say so — which needs §3's binding
   flag.
