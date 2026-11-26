<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 45 An empty results workbook says so before any download starts

Status: done
Source: template-e2e 30 (#1963), acceptance box 2, second half.

## What

The workbook route still answers 204 when no run exists (live network and held study solve both without dispatch). Make the request say so before a download starts: the Export tab shows "No run yet: run the study or Run LOPF" instead of an enabled Download, and the route returns a plain error with its reason rather than a silent 204. Test first at route level, then the Export tab.
