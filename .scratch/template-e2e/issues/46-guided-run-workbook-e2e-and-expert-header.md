<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 46 End-to-end: Guided run, workbook headline equals the verdict, Expert header names the run

Status: done
Source: template-e2e 30 (#1963), acceptance box 4.

## What

In the real app: `eh_site` > Guided > run the study > Expert > Export > Results workbook > Download. The .xlsx opens, its headline equals the Guided verdict, and the Expert header names the run. #1963 made the status and flow state read the held study solve; the header files (`AppHeader.tsx`, `ResultsHeadline.tsx`) were held by other PRs, so no header change was made. Add the journey check and any header change needed.
