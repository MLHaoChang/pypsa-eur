# PLEXOS fixtures

| File | What | Licence |
|---|---|---|
| `world2015_irl.xml` | Node EU-IRL and everything that touches it, cut from **PLEXOS-World 2015 Gold V1.1** (Brinkerink & Deane, Harvard Dataverse, [doi:10.7910/DVN/CBYXBY](https://doi.org/10.7910/DVN/CBYXBY), saved with PLEXOS 8.300) by `make_fixture.py` | CC0-1.0, as the dataset states: "made available under a Creative Commons CC0 license" |
| `edge_cases.xml` | Hand-written rows for what the World model lacks: bands, dates, several tags on one row, unreadable values, and one broken row per guard | CC0-1.0 |

Hand checks on `world2015_irl.xml` (2026-11-17, against the 130 MB source):

- 63 generators sit on EU-IRL. An independent membership count over the full source gives the same number.
- Generator `IRL_Bio_WestOffalyPow13313` has Max Capacity 137 MW: `t_data` 125588 on membership 16886 in the source.
- The per-table row counts printed by `make_fixture.py` are pinned in `tests/gridspine/test_plexos_xml.py` as `WORLD_IRL_COUNTS`.
