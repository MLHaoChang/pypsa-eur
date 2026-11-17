# Golden route-surface fragments

A pull request that adds a route handler to `routers/{results,simulation,
compare,asset_results}.py` declares which golden-coverage surfaces it reports
by adding one file here, `<branch>.json` (as in `doc/release-notes.d`), instead
of a row in `ROUTE_SURFACES` in `tests/test_golden_coverage.py`. So two pull
requests never edit the same lines (RETRO-FRAG-1). Changing or deleting an
existing row stays an edit where that row lives.

```json
{"routes": {"routers/results.py::get_thing_view": [],
            "routers/results.py::get_thing_costs": ["some_coverage_surface_id"]}}
```

The key is `<file relative to the backend>::<handler function>`; the value
lists `tests/golden/coverage.py` `SURFACES` ids, `[]` for a handler that reports
no CAPEX or fixed-cost economics. The checks run on the merged table: a row
the test file or another fragment already defines, a handler that does not
exist, and a surface id that is not real all fail.
