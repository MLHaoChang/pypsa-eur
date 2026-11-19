# PLEXOS fixtures

| File | What | Licence |
|---|---|---|
| `world2015_irl.xml` | Node EU-IRL and everything that touches it, cut from **PLEXOS-World 2015 Gold V1.1** (Brinkerink & Deane, Harvard Dataverse, [doi:10.7910/DVN/CBYXBY](https://doi.org/10.7910/DVN/CBYXBY), saved with PLEXOS 8.300) by `make_fixture.py` | CC0-1.0, as the dataset states: "made available under a Creative Commons CC0 license" |
| `csv_files/*.csv` | The first 48 hours of three of the dataset's data files (demand, wind, CSP), cut to the columns that name an object of `world2015_irl.xml` by `make_fixture.py --csv`; cells as written. The CSP file keeps the dataset's spelling `Csp` (the XML says `CSP`) | CC0-1.0, as above |
| `edge_cases.xml` | Hand-written rows for what the World model lacks: bands, dates, several tags on one row, unreadable values, and one broken row per guard | CC0-1.0 |

Hand checks on `world2015_irl.xml` (2026-11-17, against the 130 MB source):

- 63 generators sit on EU-IRL. An independent membership count over the full source gives the same number.
- Generator `IRL_Bio_WestOffalyPow13313` has Max Capacity 137 MW: `t_data` 125588 on membership 16886 in the source.
- The per-table row counts printed by `make_fixture.py` are pinned in `tests/gridspine/test_plexos_xml.py` as `WORLD_IRL_COUNTS`.

Hand checks on `csv_files/` (2026-11-17, against the dataset's CSVs):

- `All Demand UTC 2015.csv` writes midnight as a bare date (`01/01/2015`, then `01/01/2015 01:00`); the Data Files' `Locale` attribute is 2057 (en-GB), so dates are day first.
- The fixture adds generator `ESP_Csp_ANDASOL316855` (Max Capacity 150 MW) with its head storage `ESP_Csp_ANDASOL316855_Storage` (Max Volume 1.125 GWh, Initial Volume 0, Natural Inflow from `Global CSP Data`), and its node EU-ESP. PLEXOS-World's 73 head storages are all CSP thermal stores; it has no hydro reservoirs.
