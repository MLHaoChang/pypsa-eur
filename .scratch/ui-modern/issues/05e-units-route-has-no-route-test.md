<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# No route-level test for the asset units route

Status: done (#1481)
Type: follow-up
Gated: GK B

Follow-up of 05c (#1448). Confirmed real on master: `tests/test_asset_schema_units.py` called `size_units` directly; nothing hit `GET /api/network/asset_schema/{class}/units/{name}`. Added route tests (tonne-bus generator t/h, energy-bus link empty, 400 unknown class, 404 unknown asset) in the same file.
