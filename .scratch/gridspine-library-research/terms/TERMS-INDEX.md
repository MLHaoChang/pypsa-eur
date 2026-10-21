# Terms index: prioritized library sources

Prepared 2026-10-21 by an agent for the owner's clearance of `public_terms_review` sources before they enter the shared (`common`) library. Not legal advice. One page per source sits beside this file (`<source-id>.md`) with the supporting quotes.

Owner decisions already taken (2026-10-21): the owner clears each `public_terms_review` source personally; URDB and ENTSO-E are user-scoped imports only and never ship (neither is in this set).

**Reading the table.** "Recommendation" is the agent's, with the reason in one line. "Owner decision" is empty for the owner to fill. Unknown means not verified; nothing is guessed.

**Caveat on quotes.** The fetch tool often returned summaries or short extracts, not full page text. Each page marks which quotes are verbatim from a raw fetch (downloaded files, curl) and which are tool extracts or paraphrase. Where a terms page could not be read (404/403/503), the dependent cell is UNKNOWN and the recommendation is not `common`.

| Source | Catalog status | Recommendation | Reason | Owner decision |
| --- | --- | --- | --- | --- |
| [EU-C06](EU-C06.md) 50Hertz grid-access price sheets | public_terms_review | user-scoped only | Imprint forbids reproducing content or automated download without prior written consent; no licence found | |
| [EU-C07](EU-C07.md) Netze BW price sheets | public_terms_review | user-scoped only | Only "Alle Rechte vorbehalten" found; no terms of use; files themselves not opened | |
| [EU-C08](EU-C08.md) Liander tariffs | public_terms_review | user-scoped only | Disclaimer bans copying/distributing without written consent; personal download allowed | |
| [EU-C09](EU-C09.md) ACM TenneT 2026 decision | public_terms_review | user-scoped only (for now) | ACM site says government works "can be copied and published" unless marked protected, but that is a site statement, not a licence; attachments unchecked. Best candidate to upgrade | |
| [EU-C10](EU-C10.md) CRE TURPE 7 | public_terms_review | user-scoped only | Legal notice allows reproduction only unaltered, with source/date and link; commercial use not stated; calculator terms unread | |
| [EU-C11](EU-C11.md) CNMC 2026 tolls | public_terms_review | user-scoped only | Permissive terms found only for data.cnmc.es (CC BY-SA 4.0 on one page); the cnmc.es legal notice returned 404/403 | |
| [EU-C12](EU-C12.md) e-distribuzione / ARERA | public_terms_review | user-scoped only | No reuse licence on e-distribuzione; ARERA's CC BY-SA 4.0 covers ARERA-hosted data only | |
| [cost_dea_generation](cost_dea_generation.md) DEA generation and district heating | public_terms_review | common (credit DEA) | CC BY 4.0 notice inside catalogue PDF and Excel Intro sheet. See DEA conflict below | |
| [cost_dea_storage](cost_dea_storage.md) DEA storage | public_terms_review | common (credit DEA) | Same CC BY 4.0 notice in catalogue and datasheet. See DEA conflict below | |
| [cost_dea_renewable_fuels](cost_dea_renewable_fuels.md) DEA renewable fuels | public_terms_review | common (credit DEA) | Same CC BY 4.0 notice in catalogue and datasheet. See DEA conflict below | |
| [cost_dea_energy_transport](cost_dea_energy_transport.md) DEA energy transport | public_terms_review | common (credit DEA) | Same CC BY 4.0 notice in catalogue and datasheet. See DEA conflict below | |
| [cost_dea_industrial_heat](cost_dea_industrial_heat.md) DEA industrial heat | public_terms_review | user-scoped only | CC BY 4.0 in the PDF catalogue, but the Excel datasheet carries no licence statement; extending the PDF notice to it is an inference. Owner may upgrade, or ask teknologikatalog@ens.dk | |
| [cost_us_atb_2025](cost_us_atb_2025.md) NLR ATB 2025 | public_terms_review | common (credit, carry disclaimer) | OEDI record links CC BY 4.0; the ATB disclaimer is warranty/indemnity text the owner should read | |
| [cost_pnnl_storage_2024](cost_pnnl_storage_2024.md) PNNL storage DB | public_terms_review | user-scoped only | No licence text anywhere found; the "terms" page is an abbreviation glossary; workbook not inspected for an internal licence sheet | |
| [EU-C02](EU-C02.md) SMARD | open_verified | common (credit "Bundesnetzagentur \| SMARD.de") | open_verified confirmed: CC BY 4.0 for Market data visuals and Download-section data only; SMARD's ENTSO-E upstream terms not read | |
| [EU-C03](EU-C03.md) Energinet Energi Data Service | open_verified | common (credit Energinet) | open_verified confirmed: CC BY 4.0 on all published data, commercial use stated; upstream third-party rights in single datasets unchecked | |
| [rel-eia861](rel-eia861.md) EIA-861 | open_verified | common (acknowledge source and date) | open_verified confirmed: EIA copyrights page, US government work public domain; third-party material and logo excluded | |
| [rel-oeb-scorecard](rel-oeb-scorecard.md) OEB distributor scorecard | open_verified | user-scoped only (owner may upgrade) | OGL-Ontario is stated on the OEB open-data landing page, not on the scorecard dataset page, so coverage is an inference. If accepted: common with the OGL attribution line | |
| [rel-enedis-quality](rel-enedis-quality.md) Enedis supply quality | open_verified | user-scoped only (owner may upgrade) | Dataset field says Licence Ouverte 2.0, but the licence text and Enedis conditions could not be read, so all cells are UNKNOWN. open_verified not confirmed from terms text | |

## Items needing the owner's attention

1. **DEA general disclaimer vs CC BY.** ens.dk/om-os/datapolitik says copying is allowed except images, with Energistyrelsen named, and "Det er ikke tilladt at ændre eller forvanske materialets indhold" (altering content is not allowed). This conflicts in part with the in-file CC BY 4.0 adaptation grant. It reads as a site-wide statement, which is not verified. The four DEA `common` rows assume the in-file licence governs. DEA photos and figures are not CC.
2. **Three upgrade candidates** (user-scoped now, defensible as common if the owner accepts one inference): EU-C09, rel-oeb-scorecard, cost_dea_industrial_heat.
3. **Share-alike risk.** If CC BY-SA 4.0 governs EU-C11 or EU-C12, derived library packs inherit share-alike. Another reason they stay user-scoped.
4. **Re-checks that need a browser or another network:** Enedis and Etalab licence text, the CNMC legal notice, CRE's possible bar on commercial use (one search summary said so; the fetched English page did not), the PNNL workbook, ENTSO-E's terms for SMARD upstream.
5. Nothing here is approved for automatic import; the `automatic_import_approved` flags stay false.
