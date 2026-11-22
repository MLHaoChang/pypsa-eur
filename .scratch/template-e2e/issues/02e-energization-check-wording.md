<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02e — The energization check does not say why its limit is below the schedule

Status: needs-triage

Raised on #1778 (02b-2); UX 26 accepted it as a backend follow-up.

## Problem

On `eh_concentrator_staged`, the 2027 schedule is 25 MW, and the energization check for 2027
reads "the 15 MW energized import, binding hour 2030-01-15".

- Both numbers are right: the check takes min(rating × `p_max_pu`, schedule), and the 2027
  non-firm envelope (`ENVELOPE_EVENTS`, two 150-h events from hour 336) caps it at 15 MW.
- The note (`services/.../site_peak.py` `describe()`, `grid_readiness.py` note text) does not
  name the envelope.
- `snapshot_words` prints the weather-year date (2030-01-15) for the 2027 period.

02b-2 hides "against 15 MW" beside a 25 MW row; the note (tooltip) still reads as a contradiction.

## Wanted

- The note names the cause: "15 MW energized import (the 2027 non-firm envelope, below the 25 MW schedule)".
- On multi-period networks the binding hour is labelled by period: "period 2027, 15 Jan 00:00 (weather year 2030)".
- A test on `eh_concentrator_staged`: the 2027 row's energized MW is 15, the schedule's is 25,
  and the note names the envelope and does not show a bare 2030 date.
