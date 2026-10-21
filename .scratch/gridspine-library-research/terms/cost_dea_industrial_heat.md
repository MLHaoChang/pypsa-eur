# cost_dea_industrial_heat: Danish Energy Agency industrial process-heat catalog

- Status of this page: date 2026-10-21, agent summary for owner review, not legal advice.
- What the data is: Technology Data for Industrial Process Heat (PDF catalogue, version 0006, 86 pp., Apr 2026) and Excel datasheet (Jan 2026): heat pumps, boilers, process heat.
- Data URLs: Landing page: https://ens.dk/node/3455 (catalogue https://ens.dk/media/8517/download, datasheet https://ens.dk/media/7758/download; both downloaded and inspected).
- Terms actually read: the licence notices inside the files listed under quotes; the catalogue landing page above; https://ens.dk/om-os/datapolitik (Danish, 'Disclaimer og ophavsret'); https://ens.dk/en/privacy-policy-and-use-cookies (no copyright/reuse text; privacy and cookies only); https://ens.dk/privatlivs-og-cookiepolitik (Danish, none). Could not fetch https://ens.dk/en/copyright: HTTP 404. Could not fetch https://ens.dk/ophavsret: HTTP 404. No 'Brug af data' page found by search. The Danish public-sector re-use rules that DEA cites were not found on any ens.dk page I read.

| Redistribution | Derivatives | Commercial use | Attribution | API key/account |
|---|---|---|---|---|
| conditional: PDF catalogue CC BY 4.0; datasheet UNKNOWN (no statement) | conditional: as PDF; ens.dk datapolitik says no altering; datasheet UNKNOWN | conditional: allowed per PDF's CC BY 4.0; datasheet UNKNOWN | required if CC BY applies; at minimum ens.dk datapolitik requires naming Energistyrelsen as source | none seen: files download anonymously |

Recommendation: **user-scoped only**. The PDF catalogue states CC BY 4.0, but the Excel datasheet (the file most likely to be extracted) carries no licence statement, and no ens.dk page gives one. The datasheet is presumably the same work, but that is an inference, so redistribution of the datasheet cells is UNKNOWN. Owner can upgrade to common if they accept the catalogue notice as covering the datasheet, or after asking teknologikatalog@ens.dk (address printed in the catalogue).

Secondary evidence: PyPSA technology-data (secondary, not authoritative): README (https://github.com/PyPSA/technology-data, also raw README on master) has a Licence section covering the repo code (GPLv3) and says input data may carry different terms; it does not name the DEA or CC BY. GitHub code search for "Danish Energy Agency" in that repo found only source files carrying SPDX GPL-3.0-only headers, docs (docs/addnew.rst, docs/structure.rst, docs/release_notes.rst) and a bib entry crediting "Danish Energy Agency and Energinet.dk" (2019). I did not find a DEA licence/attribution statement in README/LICENSE/REUSE; a .reuse/dep5 file was not found at that path (404), and the repo file listing could not be read (access denied for the GitHub tool, API blocked), so REUSE metadata remains unchecked.

## Supporting quotes

- Landing/fetch notes: Landing page https://ens.dk/node/3455: fetched; no licence, copyright or terms statement found. Excel datasheet https://ens.dk/media/7758/download: searched all workbook parts for "licen", "Attribution 4" and "creativecommons"; no licence statement found (unlike the other four DEA workbooks, which have a License line on the Intro sheet).
- Source: PDF front matter, https://ens.dk/media/8517/download (English)
  > "This work is licensed under Attribution 4.0 International"
- Source: https://ens.dk/om-os/datapolitik, section "Disclaimer og ophavsret" (Danish; fetched via a summarising tool, so wording is the tool's short verbatim extracts)
  > "Det er tilladt vederlagsfrit at kopiere materiale - undtagen billeder"; "forudsat, at Energistyrelsen anføres som kilde."; "Det er ikke tilladt at ændre eller forvanske materialets indhold."; "Mht. billeder, figurer, illustrationer, herunder logo, skal der indhentes tilladelse" "fra Energistyrelsen før evt. brug."
  > translation (agent): Copying material, except images, is allowed free of charge provided Energistyrelsen is named as source. Altering or distorting the content is not allowed. For images, figures, illustrations incl. logo, permission must be obtained from Energistyrelsen before use. (This is the site-wide statement; it does not mention CC. It conflicts in part with the in-file CC BY 4.0 "adapt" grant; unresolved.)
- Source: https://creativecommons.org/licenses/by/4.0/deed.en (English; licence summary, not an ens.dk page)
  > "copy and redistribute the material in any medium or format for any purpose, even commercially."; "remix, transform, and build upon the material for any purpose, even commercially."; "You must give appropriate credit, provide a link to the license, and indicate if changed" (tool extract: "indicate if changes were made")
- Source: https://github.com/PyPSA/technology-data README, Licence section (English) - SECONDARY evidence only
  > "The code in `scripts/` is released as free software under the GPLv3, see LICENSE.txt." / "However, different licenses and terms of use may apply to the various input data."
- No licence statement found at https://ens.dk/en/privacy-policy-and-use-cookies or https://ens.dk/privatlivs-og-cookiepolitik.
