<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Seeded AC PF may fail on a parallel shifted+unshifted loop

Status: needs-triage
Type: bug (edge)
Found by: DGS-SEED-1 builder, handed off.

A loop of one phase-shifting and one unshifted parallel path was reported to
converge flat but fail when `run_pf` seeds it with `lpf()`. Not a real import
topology. Small reproductions (two transformers; transformer parallel to a
line, shifts 30/-30/150) converged both ways, so there is no test yet.

Today `run_pf` does not retry: a seeded `pf` that leaves snapshots
unconverged is returned as is. Possible fix: retry flat when the seeded flow
leaves snapshots unconverged, keep the better result, log it.
