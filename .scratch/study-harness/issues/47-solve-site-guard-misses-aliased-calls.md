<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The solve-site guard misses aliased calls

Status: done (#1481)
Type: follow-up
Gated: GK B

Follow-up of 42. Confirmed real: `unrepaired_solve_sites` only matched `x.optimize(...)` calls, so `opt = n.optimize; opt()` passed. None exist today. The guard now flags any reference to a solve method (and `getattr(n, "optimize")`) not preceded by `ensure_link_port_frames`; `options.params.optimize` is excluded. Test: `test_the_guard_sees_an_aliased_solve`.
