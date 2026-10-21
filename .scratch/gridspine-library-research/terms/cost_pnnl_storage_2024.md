# cost_pnnl_storage_2024: PNNL Energy Storage Cost and Performance Database v2024
- Status of this page: date 2026-10-21, agent summary for owner review, not legal advice.
- Data: US grid energy storage cost/performance database (XLSX), per technology and duration.
- Data URLs: https://www.pnnl.gov/projects/esgc-cost-performance/download-reports ; https://www.pnnl.gov/sites/default/files/media/file/ESGC_Cost_Performance_Database_v2024.xlsx
- Terms read: https://www.pnnl.gov/projects/esgc-cost-performance/terms (fetched OK via curl, HTTP 200; WebFetch returned 403) and https://www.pnnl.gov/security-privacy (privacy notice only).
- Finding: the "Terms" page is only a glossary of abbreviations (AC, BESS, BMS, DOD, RTE ...). It contains NO licence or use terms. The download page contains no licence text either. The pnnl.gov /disclaimer, /legal, /disclaimers, /copyright, /privacy paths returned 404. Wayback (web.archive.org) connection was reset / blocked by the proxy. No data licence was found anywhere I could read.

| Redistribution | Derivatives | Commercial use | Attribution | API key/account |
|---|---|---|---|---|
| UNKNOWN: no licence found | UNKNOWN | UNKNOWN | UNKNOWN (citing the source is good practice, not a stated requirement) | No account seen on download page (not tested) |

## Recommendation: user-scoped only
No licence text could be located, so the data should not ship in a shared/common library. Users may supply the file themselves. Owner could ask PNNL (or check the XLSX's own README/licence sheet, not inspected) before promoting.

## Supporting quotes
- https://www.pnnl.gov/projects/esgc-cost-performance/terms (English): page heading "Terms" followed by table "Abbreviation / Definition" e.g. "BESS | Battery Energy Storage System", "RTE | Round Trip Efficiency". No legal text.
- https://www.pnnl.gov/projects/esgc-cost-performance/download-reports (English): "The updated Energy Storage Cost and Performance Database values provided on this webpage do not currently have an associated report."
- Could not fetch https://web.archive.org/web/2025/https://www.pnnl.gov/projects/esgc-cost-performance/terms : curl (35) Recv failure: Connection reset by peer.
- Could not fetch https://www.pnnl.gov/disclaimer , /legal , /disclaimers , /copyright , /privacy : HTTP 404.
