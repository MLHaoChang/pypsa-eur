# Phase 12a — a generator with BOTH a profile and outage data (plan, v1)

## 0. The finding, measured

Two IDENTICAL 100 MW wind farms, same 25 %-capacity-factor `p_max_pu` profile.
The only difference: one has an outage rate entered.

| farm | membership | how the MC models it | credit |
|---|---|---|---|
| `wind_no_for` | must-take (`source == "missing"`) | profile netted into the residual | **25 MW** mean, 45 MW peak |
| `wind_with_for` | sampled fleet | flat two-state at `capacity_mw`, **profile discarded** | **90 MW** = (1−q)·p_nom |

And the reserve margin, on the SAME network, credits `wind_with_for` at
`d = (1−q)·profile̅ = 0.225` → **22.5 MW**.

So the constraint says 22.5 MW and the sampler that certifies the constraint
says 90 MW, for one asset. **4×.** Entering MORE data (an outage rate) makes
the tool credit the asset ~3.6× more, because the profile is dropped.

Reproduced with `reserve_margin_facts` and `fleet_and_residual` directly; no
warning is emitted anywhere.

## 1. This is an UNHANDLED INPUT COMBINATION, not an oversight

`copt.py`'s module docstring states the split deliberately:

> an electrical, non-slack generator with resolvable occurrence params is a
> two-state COPT unit **at its firm capacity**; one without is must-take,
> netted at `p_max_pu × capacity`. VRE therefore nets via its hourly
> profiles … **its mechanical FOR stays excluded**, consistent with
> `occurrence.py`'s no-VRE-defaults decision.

The design assumes VRE does not carry a FOR — and the defaults library
deliberately supplies none. But nothing stops a user entering one by hand, and
when they do, the asset falls into the first branch and its profile is dropped
in silence.

**The reserve margin already resolves the same ambiguity the other way**:
`d = (1−q)·avail` uses BOTH factors for that identical asset
(`solver_service.py`, the peak-coincidence loop). So the product holds two
philosophies at once, and that is the defect regardless of which one is right.

## 2. Two candidate fixes, and why I am proposing the smaller one

**(a) Apply both factors everywhere** — `available = profile[h] · capacity ·
up/down`. Physically the most defensible: a forced outage and a wind resource
are independent, and this is what the margin already assumes. But it changes
the sampled-fleet model inside a **benchmarked** engine (RTS-79 / RBTS), needs
a per-unit profile threaded through `CoptUnit` → `MCInputs` → `simulate`, and
has no clean analogue in the COPT, whose static convolution has no hour axis.
It also contradicts a documented decision rather than filling its gap.

**(b) Refuse the ambiguous combination at preflight** — the house pattern.
This project already refuses rather than guessing when evidence is missing:
`reserve_margin_unpriceable_assets` excludes and refuses an asset instead of
defaulting its derate to 1.0, on the stated rule that **nothing may default to
1.0**. Silently crediting a profiled wind farm at ~0.9 availability is that
same rule broken, in the same direction.

**Proposed: (b) now, (a) recorded as a decision for its own phase.** (b) is
low-risk, needs no engine change, cannot move a benchmark anchor, and makes
the user resolve a modelling question only they can answer. (a) is a real
improvement but it is an engine change that deserves its own review, its own
benchmark re-run, and evidence about how often the combination occurs.

## 3. Scope

**IN**
- A preflight issue raised when an electrical, non-slack generator has BOTH a
  resolvable outage rate AND an hourly `p_max_pu` profile that is not
  identically 1.0. It names the asset, states which of the two the engines
  will use and which they will ignore, and states the direction of the error.
- Raised on the shared occurrence/preflight path so it reaches **both** the
  reserve-margin preflight and a plain MC study — a user who never touches the
  margin still gets a silently-wrong LOLE today.
- The reserve-margin panel renders it beside the existing unpriceable-assets
  copy, which is the same shape of problem.

**OUT (recorded, not skipped)**
- Changing the engines' model of such a unit — §2(a), its own phase.
- The COPT's hour axis. It has none; that is a fidelity statement the module
  already makes.

**Blocking or warning?** Warning, not blocking, and this is the one real
judgement call in the plan. A blocking error would refuse to solve a network
that solved yesterday, on data the user deliberately entered. A warning that
names the asset and the 4× direction gives them what they need without
breaking their workflow. (Contrast `unpriceable_assets`, which blocks because
there the tool has NO basis to credit the asset at all; here it has two.)

## 4. Acceptance

★ **A1** — the two-farm fixture of §0 raises the issue for `wind_with_for` and
NOT for `wind_no_for`. Derived from the fixture, never hardcoded.
★ **A2** — a thermal unit with outage data and a flat `p_max_pu` of 1.0 does
NOT raise it. This is the false-positive guard: every conventional generator
in every existing project has occurrence data, and most carry a trivial
profile column. If this fires there, the warning is noise and will be ignored.
★ **A3** — the issue states the DIRECTION (the asset is credited at its outage
rate, its profile ignored, so its adequacy contribution is OVERSTATED), not
merely that a conflict exists. A warning that does not say which way it errs
cannot be acted on.
★ **A4** — the benchmark anchors (RTS-79, RBTS) are untouched, bit-for-bit:
this phase adds no engine change, and the test proves the claim rather than
asserting it.
★ **A5** — it reaches a plain MC study, not only the margin preflight.

## 5. Open questions for the review

1. Is (b)-now/(a)-later right, or is the silent 4× bad enough that (a) must be
   this phase despite the benchmark risk?
2. Where exactly does the check belong so BOTH surfaces get it without a
   second implementation of the membership rule? (`_membership_walk` is the
   single walk both `fleet_and_residual` and `must_take_generators` use.)
3. What counts as "has a profile"? A `generators_t.p_max_pu` column that is
   identically 1.0 is not a resource profile. Is a static `p_max_pu < 1`
   (no time series) the same case, or a derate the user set deliberately?
4. Does the same ambiguity exist for storage (`p_max_pu` on a StorageUnit with
   outage data), and if so is it in scope?
