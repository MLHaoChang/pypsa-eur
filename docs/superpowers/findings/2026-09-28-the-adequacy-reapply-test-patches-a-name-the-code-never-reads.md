# `test_contingency_mutation_survives_the_user_ts_reapply` cannot fail for the regression it names

**Date:** 2026-09-28
**Severity:** MEDIUM — no product defect. A guard that protects adequacy-sweep
results is unprotected by the test written to protect it, so removing the guard
is a silent, suite-green regression.
**Status: OPEN, NOT FIXED.** Found while making the user time-series store
per-`ProjectContext`. Left alone on purpose: it is a test-efficacy defect in the
adequacy sweep, not part of the tenancy fix, and changing a test's meaning is a
decision for whoever owns that suite. The remedy below is verified, not proposed.
**Basis: reproduced.** Four measured runs, all on `python3.12` against the
tree at `HEAD`. This is NOT a consequence of the per-context store — the same
flaw holds on the pre-fix code, for the same reason.

## The guard being protected

`services/solver_service.py` skips `_reapply_user_ts_to_network` when
`network._adequacy_transient_profiles` is set:

```python
if _is_foreground and not _transient_profiles:
```

It earned its place. The class-B/C adequacy sweep works by MUTATING the very
`_t` tables that reapply restores — a class-C scenario scales
`loads_t.p_set` / `generators_t.p_max_pu`, a class-B link outage zeroes
`links_t.p_max_pu`. The sweep runs on the foreground network, so without the
second condition the pristine uploaded profile is written back over the
mutation before the LP is built. The contingency then solves an UNMUTATED
network, returns "ok", and reports ΔEUE = 0 — a cold snap plus Dunkelflaute
priced at exactly zero criticality, which reads as a successful measurement
rather than a failure to measure.

## Why the test cannot see it

`tests/test_adequacy_stress.py`:

```python
store = {("loads", "p_set", "l"): pd.Series([100.0] * N, index=n.snapshots)}
monkeypatch.setattr(network_router, "_user_ts", store, raising=False)
```

That rebinds the name `_user_ts` on the module `routers.network`.
`_reapply_user_ts_to_network` is DEFINED in `services/user_timeseries.py`, so
its `__globals__` is that module's namespace and it resolves `_user_ts` there.
`routers.network` only re-exports the name; rebinding the re-export cannot
change what the function reads. The store the reapply actually consults stays
EMPTY for the whole test, so the reapply is a no-op whether the gate is there or
not — and the assertion passes for a reason that has nothing to do with the gate.

## Measured

| store | gate | result |
|---|---|---|
| monkeypatched re-export (as committed) | intact | **pass** |
| monkeypatched re-export (as committed) | `not _transient_profiles` removed | **pass** ← should have failed |
| real store (`services.user_timeseries._user_ts`) | intact | **pass** |
| real store | `not _transient_profiles` removed | **fail**, with the test's own message: "the stress scenario measured no degradation — the uploaded profile was reapplied over the mutation before the LP was built" |

Row 2 is the defect: the regression the test is named for, applied, suite green.
Row 4 is the positive control — it establishes that the guard does real work and
that the test's assertion is sound. Only its wiring is wrong.

## The remedy, as measured

Replace the monkeypatch with a write to the store the code under test reads:

```python
from services.user_timeseries import _user_ts as _real_store
_real_store.clear()
_real_store[("loads", "p_set", "l")] = pd.Series([100.0] * N, index=n.snapshots)
```

That is exactly what produced rows 3 and 4. It needs no `monkeypatch` fixture —
the autouse `reset_backend` fixture in `tests/conftest.py` already clears the
store around every test. The test writes on the same thread it calls
`PyPSAService.set_network(n)` on, so the view resolves that context's store.

## The shape worth remembering

`routers/network.py` re-exports ~60 names from the carved service modules, and
`tests/test_network_facade_surface.py` pins that each is the SAME OBJECT as the
service's. Monkeypatching the re-export satisfies nothing that test checks and
reaches no function defined in the service. Any test that patches a name through
the facade rather than at its definition has this bug; this is one confirmed
instance, not a survey.
