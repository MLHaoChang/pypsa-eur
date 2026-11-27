# Metric-ledger fragments

One file per checked-answer profile in `harness.calculations.PROFILES`, named
after the profile id with every run of non-alphanumerics turned into `-`:
`bus-price/1` is `bus-price-1.json`. A pull request that adds a profile adds its
file here; one that adds a metric to a profile adds a row to that profile's
file. `smoke/json_fragments.py` merges the files with `../metric_ledger.json`
(which holds no rows), and `../test_metric_ledger.py` checks the result.

```json
{"profiles": {"bus-price/1": [
  {"metric": "mean_price",
   "formula": "sum over t of w[t] x price[t] / sum over t of w[t]",
   "validity": "the guards and reason codes the code applies",
   "tolerance": "exact",
   "oracle": "pypsa-gui/backend/tests/test_x.py::test_y[param]",
   "status": "verified",
   "domain_review": "pending",
   "surfaces": {"answer": {"status": "verified", "test": "pypsa-gui/backend/tests/test_x.py::test_y"},
                "workbook": {"status": "pending", "test": null, "ticket": ".scratch/<feature>/issues/NN-x.md"}}}
]}}
```

- `domain_review` is `pending` or `gk-b:<40-hex sha>`, the head GK B gave its OK
  on. Only a `verified` row whose named tolerance constant appears in its
  oracle test's module may hold a sha. The optional `domain_review_note` says
  why a row stays pending. The field feeds no tally and no gate.
- One profile per file, one row per metric, no other keys. A `(profile, metric)`
  that two files define fails, naming both.
- `formula` is plain text over the raw inputs; `validity` names the guards and
  reason codes from the code.
- `tolerance` is `exact`, `ulp:<n>`, or the named constant the metric's code
  applies, as `module.path:NAME` (it must import).
- `status` is `verified` only when `oracle` names a test that asserts this
  metric against a number written by hand in the test, not one computed through
  the service or a shared helper (`fact_oracles.py` does not count). Otherwise
  the row is `pending` and `oracle` is `null`.
- `surfaces` maps any of `answer`, `route`, `workbook`, `pdf`, `download_meta`
  to the same `status` / `test` pair, judged by the same rule on that surface.
  A verified surface needs a verified row.
- `ticket` (on a row or a surface) is an existing file under `.scratch/`.
- Node ids are repository-relative and are resolved statically: the file, the
  test (or `Class::method`) and, for `name[param]`, a param id its one
  `parametrize` decorator lists as literals.
