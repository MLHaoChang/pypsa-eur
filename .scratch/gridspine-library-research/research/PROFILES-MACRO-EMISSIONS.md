# Profiles, economic normalization and emissions sources

Status: research snapshot, 2026-10-20. Generated publication index over the saved structured research; final cross-source review and ingestion validation remain pending. These records identify candidates, not approved populated library values.

| Source | Reuse/access assessment | Proposed integration |
| --- | --- | --- |
| [ECB Data Portal exchange rates and statistical reference series](https://data.ecb.europa.eu/help/getting-data-web-services-sdmx) | public_terms_review: Public statistical API; bounded requests | Pinned economic series + explicit derived money-basis transformation |
| [Eurostat HICP/PPI and non-household energy-price benchmarks](https://ec.europa.eu/eurostat/en/web/user-guides/data-browser/api-data-access/api-introduction) | open_verified: Public API/download | Economic series and benchmark items; no fabricated tariff schedule |
| [BLS public CPI/PPI/labor statistics](https://www.bls.gov/developers/home.htm) | public_terms_review: API v1 public limited; v2 registration/limits | Versioned economic series; bounded documented connector |
| [Statistics Canada Web Data Service](https://www.statcan.gc.ca/en/developers/wds) | open_verified: Public WDS and table downloads | Canada-specific reference series with original table/PID and release |
| [Bank of Canada Valet](https://www.bankofcanada.ca/valet/docs/) | public_terms_review: Public API with usage limits | Pinned CAD reference data; no implicit finance assumptions |
| [INEGI indicators and downloadable statistics](https://www.inegi.org.mx/servicios/api-indicadores/) | public_terms_review: API token for indicator service; public downloads available | Mexico-specific economic series and explicitly chosen cost escalation |
| [Banco de Mexico SIE time-series API](https://www.banxico.org.mx/SieAPIRest/swagger/index.html) | unknown: Token-managed public API; catalog visible without data request | Metadata-first connector candidate; scoped rights review before distribution |
| [Copernicus ERA5 single-level hourly reanalysis](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=overview) | open_verified: CDS account, dataset terms acceptance and API; no bulk download performed | Source/version-pinned weather references; derived regional profiles through existing tooling |
| [JRC PVGIS non-interactive API](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en) | public_terms_review: Public API, version/rate-limited | Optional profile oracle/source; do not duplicate native renewable-profile engine by default |
| [NLR NSRDB solar resource APIs](https://developer.nlr.gov/docs/solar/nsrdb/) | public_terms_review: Developer key and usage limits; dataset endpoints differ | Optional bounded source connector and native profile generation |
| [ComStock public end-use profiles](https://comstock.nlr.gov/page/datasets) | public_terms_review: Public release downloads; no bulk retrieval performed | Curated synthetic archetypes with release/weather/upgrade and known-issue metadata |
| [OPSD contributed household/small-business measurements](https://data.open-power-system-data.org/household_data/) | public_terms_review: Public versioned downloads; metadata only inspected | Small reference/QA fixture or explicitly scoped shape; never generic mine/DC profile |
| [US EPA eGRID electricity emissions factors](https://www.epa.gov/egrid) | open_verified: Public downloads | Versioned emissions-factor benchmark with explicit accounting boundary |
| [EEA electricity generation GHG intensity](https://www.eea.europa.eu/en/analysis/indicators/greenhouse-gas-emission-intensity-of-1) | public_terms_review: Public indicator/data catalog | Versioned emissions reference; separate from carbon market price |

Read [profile-macro-sources.json](profile-macro-sources.json) for publisher, region, parameter families, terms URL, cadence and limitations. Public access does not automatically authorize a shared or commercial data library. Historical prices, public awards and synthetic profiles are not project quotes, forecasts or measured customer data.
