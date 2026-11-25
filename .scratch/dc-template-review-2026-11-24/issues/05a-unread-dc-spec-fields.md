<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05a Three DC spec fields are accepted and read by no code

Status: needs-triage
Blocked by: none
Source: found by `tests/test_dc_spec_fields_read.py` (DC review 05, AC3).
Severity: minor

## Problem

The field-read guard lists each accepted spec field that no non-test backend code reads. Three are allowlisted as KNOWN UNREAD:

- `DataCentreLoadSpec.workload_shift_window_h`: `dc_it_energy.py` documents that it moves no load (DC 06).
- `GensetSpec.power_mw`: the fleet size comes from the template; the spec value is only a constructor placeholder in `dc_backup`.
- `GensetSpec.fuel_price_ref`: the backup block's `fuel_price_per_mwh_fuel` prices fuel instead.

## Acceptance criteria

- [ ] Each field is modelled (a code path reads it, with a test) or removed from the schema (old stored specs still load).
- [ ] Its entry leaves `READ_ELSEWHERE` in `tests/test_dc_spec_fields_read.py`.
