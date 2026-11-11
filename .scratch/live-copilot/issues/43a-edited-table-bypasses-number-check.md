<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-43a — An edited Word table changes an admitted number unflagged

Status: needs-triage
Type: bug

Found by LC-43 (`tests/test_lc43_d21_report_gates.py`); GK B (46) ruled it a defect.
The manifest row `LC-D21.edited_admitted_number` stays pending until this is fixed.

## What happens

1. Generate the study report (MC LOLE 3.21 h/yr in the `headline` table) and export
   its DOCX.
2. In Word, change the headline cell "MC LOLE (h/yr)" to "1.00 h/yr".
3. `import_edited_report(report_id, file_id)`, with `bind_as_template` false or true.

Version 2's `executive_summary` becomes `source: user_edit`, and its `headline`
TableRef is replaced by `headline_edited_v2`. The exported v2 DOCX headline then reads
"1.00 h/yr", with no "Numbers to check" entry and no audit. `get_report_table(rid,
"headline")` still reads 3.21, and v1 is intact.

## The bypass

The roundtrip's edited-table path (`services/reports/roundtrip.py`, a table becomes
`<id>_edited_v<next>` in `edited_tables`) never goes through
`services/reports/revisions.py::_number_reason`. That is the check that stops a
regenerated or comment-anchored edit from changing an evidence-bound number.

## Done when

An imported table edit that changes an evidence-bound number either is refused or
is listed in "Numbers to check" (unverified, withheld from the headline), under both
`bind_as_template` values. A test pins that, and `LC-D21.edited_admitted_number` in
`pypsa-gui/backend/smoke/family_manifest.py` turns covered.
