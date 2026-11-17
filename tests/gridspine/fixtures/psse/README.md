<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PSS/E RAW fixtures

The `.raw` files are PSS/E v33 case files copied unchanged from the test data of
PowerModels.jl (`test/data/pti/`, commit `4c14405670fe85596388a23320ad9d3660e1c033`),
licensed BSD-3-Clause; the full licence is `LICENSE.PowerModels.md`. Each file has
a `.license` sidecar for REUSE.

They test `gridspine/ingest/psse_raw_import.py` against files the importer did
not write: switched shunts and bus names (`case14`), three-winding transformers
(`three_winding_*`, `case4_3wtf_*`, CW=2/3 and CZ=2), magnetising admittance
(`two_winding_mag_test`), DC links the importer lists as not mapped
(`two-terminal-hvdc_test`, `vsc-hvdc_test`) and empty fields that take PSS/E's
defaults (`parser_test_defaults`).
