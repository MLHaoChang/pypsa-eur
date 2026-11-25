<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The MIP price gate reads the applied solver record

Status: done (#2016)
Type: task
Found by: the AA-18 builder's hand-off on #2014 (GK B: out of scope there).
Parent: 18-record-applied-solver-options.md

## Problem

#2014 seals the options each solve applied in
`network.meta["assistant_solve_applied"]` (`applied_record_of` in
`harness/solve_validity.py`), and `applied_gap` and `is_mip` read it. The
price gate's readers still judge partly from persisted state:

- `solved_price_limit` returns `last_solve_settings["price_limit"]` before it
  looks at the network. Persisted settings therefore win over the record that
  the network carries.
- `price_limit_without_config` reports `solver_config_unreadable` for a
  committable network even when the record settles the question without any
  config.
- `mip_price_limit` already reads the record through `is_mip`, but no test
  pins that the record wins over a disagreeing config.

## Acceptance

- A run whose applied record and config (or settings) disagree reports the
  record's verdict in all three readers.
- A run sealed before the record existed, or one whose record is malformed,
  keeps today's settings-then-config path (fails closed when malformed).
- One test drives the real island path (`services/solver/island.solve_uc`)
  through `run_simulation` and reads the sealed record.
- Whether non-scalar solver options (today named under `dropped`) are kept in
  the record is decided with GK B.

## Comments

GK B (71), plan GO: non-scalar solver options stay out of the record (option A);
`dropped` keeps naming them. Done in #2016.
