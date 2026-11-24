<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# An in-memory template build depends on what the process built before

Status: needs-triage
Type: bug
Found by: issue 225 (#1949), CI shard 3 on 1c0b6d78; GK B 70 asked for this ticket.

## Problem

PyPSA registers a Link's extra port 2 (`bus2`, `efficiency2`) process-wide once any network uses one. `eh_concentrator.py:203` adds the stage's `grid_import` with `efficiency2=-eff` and no `bus2`. In a fresh process, that Link's `efficiency2` is NaN. After any network with a port-2 Link has been built (for example the full `build_eh_concentrator_staged(T)`), it reads 1.0.

So the same `build_eh_concentrator_staged(T, years=(2027,))` gives different input fingerprints, depending on process history:
- fresh: `0ba25ecd…` / `5c081ccc…`
- after a port-2 build: `bdb0aeb4…` / `6232901d…`

The same holds on master 5e8cbcdc. Saved projects load from netCDF, so their stored fingerprints are stable. What can differ is an in-memory build compared with a build in another process, such as a template preview or a test pin.

## Repro

```python
a = C.build_eh_concentrator_staged(T, years=(2027,))
C.build_eh_concentrator_staged(T)
b = C.build_eh_concentrator_staged(T, years=(2027,))
a.links.loc["grid_import", "efficiency2"], b.links.loc["grid_import", "efficiency2"]  # nan, 1.0
```

## Ideas

- Give `grid_import` (and any other Link given an `efficiency{k}` without its `bus{k}`) an explicit value, or don't set `efficiency2` without `bus2`.
- Check the other templates for links that set `efficiency{k}` without `bus{k}`.

## Comments
