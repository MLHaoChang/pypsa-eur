# European commercial sources: PPAs, CfDs, swaps and reference markets

## PPA and power agreement files

The current [Energy Traders Europe PPA page](https://www.energytraderseurope.org/our-contracts-overview/power-purchase-agreement) links a full V1.0 PPA PDF, execution-pages DOCX, general terms and several country translations. The official full-file link discovered is https://cms.energytraderseurope.org/storage/uploads/media/190618-lc-st-power-purchase-agreement-v10---full-version.pdf . This is useful contract discovery, **not a finding that these documents are open-source or freely redistributable**.

The inspected [site terms](https://www.energytraderseurope.org/terms-of-use) restrict reproduction, derivative/commercial exploitation and redistribution without permission. A specific agreement may have separate permissions, but those have not been established here. Keep links and rights findings; no agreement text/file is copied or converted into a common seed. A user-owned contract still needs actual permission for the intended processing/use.

The EU Commission's [electricity-market design page](https://energy.ec.europa.eu/topics/markets-and-consumers/electricity-market-design_en) is a policy reference for PPAs and two-way CfDs, not a market-price feed. Fresh ACER PPA monitoring requests returned 503; prior discovery is retained without claiming fresh access.

## Match the product to the native model

| Product/data | Native subset | Remaining semantic contract |
| --- | --- | --- |
| Physical renewable PPA | PpaContract kind, price, tenor, assets, share, volume/baseload, escalation and supported sleeving terms | Delivery point, losses, shape/imbalance, certificates, volume risk, negative prices and actual invoice eligibility |
| Financial/virtual PPA | PpaContract settlement=financial and reference price | Only the supported asset/volume/reference settlement; no claim of an arbitrary derivatives portfolio, collateral, counterparty-risk or mark-to-market engine |
| Fixed-for-floating power swap | A mathematically compatible financial PPA/CfD subset may express a chosen linear settlement | Explicit long/short sign, fixed/floating legs, notional/load shape, interval, index, tenor and currency. Do not map caps, swaptions, basis swaps or complex commodity structures to a simple PPA silently. |
| Public renewable CfD award | CfdContract strike/reference/tenor subset | Auction vintage, delivery years, indexation money year, reference calculation, negative-price rules, volume and statutory scheme terms; award price is not a bilateral quote |
| Hydrogen auction support | Dated support/incentive reference | EUR/kg premium is not a hydrogen sale price or currency/MWh electricity CfD strike; eligibility and additionality are separate |
| Forward/futures curve | A permitted pinned scenario/series can be an input | Historical spot is not a forward curve. No unrestricted commercial EU power/swap forward feed was established. |
| Guarantees of Origin | Certificate/reference subset | AIB statistics describe issuance/transfers/cancellation, not traded GO prices or automatic hourly clean-energy/H2 certification. |

The existing native implementation already has physical/financial PPA settlement and contract price/share fields. Research should improve source-backed inputs and explicit unsupported clauses, rather than install a second contract engine merely to reproduce that subset.

## Data access findings

[EEX's market-data hub](https://www.eex.com/en/market-data/market-data-hub) identifies the relevant power-market data products. Public pages and auction reports do not establish a licence for unrestricted real-time or historical forward-curve redistribution. Carbon-auction records are a different product from electricity futures. Retain an explicit licensed-source/BYO path where appropriate and permitted; do not scrape around access controls.

Published awards, announcements and price-index reports are often selective, lagged or aggregated. Capture country/zone, technology, capture profile, delivery period, tenor, currency/base year, publication/observation date and rights. They may support labelled ranges or QA checks; they do not fill a private corporate PPA price automatically.

Two additional EU award sources were inspected directly. [Bundesnetzagentur completed onshore wind auctions](https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/Ausschreibungen/Wind_Onshore/BeendeteAusschreibungen/start.html) publish round-level weighted awards, min/max bids and related statistics. Those EEG support values are neither corporate PPA quotes nor automatically equivalent to a symmetric two-way CfD. The [EirGrid RESS page](https://www.eirgrid.ie/industry/renewable-electricity-support-scheme-ress) links final RESS 5 results and a RESS 4 results XLSX, both actually obtained. **The final RESS 5 report explicitly prohibits reproduction without prior written permission** (page 3). It also records a disqualification after provisional results: distinguish final versus provisional and round-specific terms. No report/table is mirrored into common data; the workbook needs its own rights determination.

OpenSourceRisk/Engine and QuantLib were inspected as **software candidates only**. Repository metadata did not resolve a specific SPDX licence, but QuantLib's actual LICENSE.TXT was then inspected: it permits source/binary redistribution with retained notices and a no-endorsement clause, in BSD-style terms. ORE's selected licence URLs were not resolved. No library is installed and no full source/instrument audit is claimed. Even a validated pricing engine supplies neither an entitled market curve nor a current counterparty quote. A valuation/risk adapter should be a later scoped feature if users need it, not a prerequisite for tariff/PPA input libraries.

## Proposed next contract-data slice

Add rights-cleared structured archetypes authored around native fields, with every commercial number user-supplied or explicitly synthetic/benchmark. Preserve a separate document/source reference and unsupported-clause list. Test zero/negative reference prices, interval and monthly-capture settlement, generation-share versus baseload notional, buyer/seller signs, indexation/base year and double counting with merchant revenue. Never import legal text as executable instructions or infer legal suitability.
