<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The exact fingerprint hashes PyPSA's process-wide component defaults table, so it can be order-dependent

Status: done
Size: M
Blocked by: none
Gated: GK B
Likely files: pypsa-gui/backend/services/workflow_tools.py (`_network_input_fingerprint`); pypsa-gui/backend/tests/test_energization_per_period.py
Type: task (engine)
Found by: SCU06 (4), PR comment on #1880.

## Problem

With `exact=True`, `_network_input_fingerprint` hashes `defaults.loc[columns, ['type', 'unit', 'status']]` for every component. `component.defaults` is PyPSA's table, shared by the whole process. The plain fingerprint does not read it. A test elsewhere in a shard that changes that table (a Link port test that drops and re-adds defaults rows, a custom attribute) can therefore move the exact fingerprint of an unrelated network, and a pin like `MASTER_FP_EH_DATACENTER` then fails depending on which tests shared an xdist worker.

SCU06 (4) saw the d2a failure on #1880's CI, passing locally and on re-run, and checked two candidates that did not reproduce it (a network built first with a 6-port Link; `test_link_port_frames.py`). That failure was the CPU-dependent `np.exp` of #1881, not this. This ticket is the separate risk that the table is still shared mutable state in the hash.

## Expected

- Find out whether any test or code path mutates a component's defaults table in the process (grep for writes to `.defaults`, custom attributes, registered components), and name it, or show that none does.
- If one does: isolate it (restore on teardown), or hash only the columns the network actually carries, so the fingerprint does not read the shared table. A fingerprint change moves every pin, so it needs GK B review and every pin listed old → new.
- A test that mutates the table, restores it, and asserts the exact fingerprint of a template is unchanged either way.

## Notes

Not reproduced. If nothing mutates the table today, close as wontfix and keep the test as a guard.

## Resolution

Done by #1874, not by a defaults-table fix. The table was never the cause. `eh_templates._week()` returned the module-level `SNAPSHOTS` Index, which a template's time-series frames used as their own `index=`; a solve names that Index "snapshot" in place, so a later eh_datacenter in the same process hashed `index_names` as `[["str","snapshot"]]` instead of `[["missing"]]`. Only the exact fingerprint reads index names, so the plain one matched. Seen on #1849 shard 1, #1880 shard 8 and #1692 shard 3 (gw1 ran `test_dc_backup.py`, which solves an eh_datacenter, before `test_energization_per_period.py`).

Fixed on master by cbc6347d (`_week()` returns a copy), 588afab8 (every builder copies the module index) and 6e6da228 (frames indexed by `n.snapshots`). Checked on master 01b1d98b: `pytest tests/test_dc_backup.py tests/test_energization_per_period.py` in one process, 147 passed. On 9bfe70ec7 the same order failed d2a.
