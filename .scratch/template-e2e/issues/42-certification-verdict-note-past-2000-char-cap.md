<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 42 The `certification_verdict` note can run past the 2000-character cap

Status: needs-triage
Source: GK A nit on #1927

## What

`certification_verdict` returns a `(verdict, note)` pair, and `bounded_verdict` in `critical_feeders` can append more text to the note. With many critical feeders the note can run past 2000 characters. The adequacy text cap is `MAX_TEXT_LEN = 2000` (`pypsa-gui/backend/services/adequacy/worksheet.py:35`, `asset_health.py:49`). On master 7677384a2 I found no check or trim on the verdict note. Confirm which cap #1927 meant, then trim or fold the note at that cap and add a test with many feeders.

## Where

`pypsa-gui/backend/services/adequacy/eh_study.py` (`certification_verdict`, line 463; its caller near line 611) and `pypsa-gui/backend/services/adequacy/critical_feeders.py` (`bounded_verdict`).
