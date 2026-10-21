# GridSpine European data-library overview

The focused package maps **44 parameter groups** to **36 source assessments** for European grid tariffs, commercial contracts, renewable/hydrogen costs, technical performance, degradation and maintenance. It reconciles the earlier research with current native support; source counts overlap and are not distinct approved databases.

**Implemented:** an optional backend Library reference reader with **24 explicit technology/year snapshots** for onshore wind, utility PV, commercial rooftop PV, 100 MW AEC and 100 MW PEM. It returns native cost models with source cells, hashes, money years, capacity basis and exclusions. The selected DEA workbook subset is attributed CC BY 4.0. Existing defaults and projects remain unchanged; UI/chat selection is subsequent work. The defaults pack (with #499's regional versions) stays the seeded default; this reader is an opt-in reference, and `compare_europe_reference_with_pack` shows both values with their own sources and money years where a key exists in both. The owner cleared the two DEA workbooks for `common` on 2026-10-21.

**Next priorities:** expose these references through the existing review/apply workflow; admit one Danish tariff API subset after rights/currency/date checks; implement selected final DE/NL schedules; then FR/ES/IT and additional jurisdictions. Contract templates, public awards, current quotes and forward curves remain separate inputs. Public access alone is not an open-data licence.

EU-wide operational tariff coverage, OEM maintenance laws and live PPA/swap quotes are not claimed. The research identifies source and schema gaps, including Portugal's half-hour tariff windows and an ambiguous SOEC lifetime field.

- [Complete research overview and priorities](../../.scratch/gridspine-library-eu/README.md)
- [Parameter inventory](../../.scratch/gridspine-library-eu/01-PARAMETERS.md)
- [Individual source assessments](../../.scratch/gridspine-library-eu/02-SOURCES.md)
- [Integration plan and validation matrix](../../.scratch/gridspine-library-eu/06-INTEGRATION-TESTS.md)
- [Implementation handoff](../../.scratch/gridspine-library-eu/HANDOFF.md)
