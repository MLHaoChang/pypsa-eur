<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-27: let the pack read-back judge a PSS/E pack

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/12-contract-connected-channel.md` (follow-up of PR-B)
Gated: yes (GK A, gridspine)

## Why

Since GC-12 PR-B the PSS/E scripts write their per-case CSVs in the read-back's dialect
(`time [s]`, `<name> [<unit>]`, `connected [status]`, contract version 2), and
`channels.read_csv` reads them. But `readback.pack.judge_pack` still cannot open a PSS/E pack:

- `psse_pack.write_psse_pack` writes no `battery.json` and no `data_hash`, so `read_manifest`
  refuses the pack;
- `KNOWN_PACKS` names only `pscad_emt`;
- the scripts write their CSV and `.status.txt` next to themselves, not under `results/`.

So a PSS/E result can be read channel by channel, but no judgment is made from a PSS/E pack.

## What to build

1. The PSS/E pack writes `battery.json` (`battery_json(cases, PSSE_CONTRACT_VERSION)`), lists it
   with SHA-256 in the manifest and carries a `data_hash`, `pack` and `pack_version`, as the EMT
   pack does.
2. `KNOWN_PACKS` accepts the PSS/E pack; the README says where the results go (`results/`).
3. A read-back test on a real `write_psse_pack` output with hand-written result CSVs: a
   stays-connected pass, a trip fail, a failed poll unknown with the sidecar reason.

## Done when

- `judge_pack` on a written PSS/E pack with results gives one judgment per battery case.
