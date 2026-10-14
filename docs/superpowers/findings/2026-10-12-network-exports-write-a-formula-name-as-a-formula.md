# The network CSV and Excel exports write a name that starts with `=` as a formula

**Date:** 2026-10-12
**Found while:** fixing OPEN-ITEMS item 8 (component names checked at the edge)
**Status:** OPEN. Recorded, not fixed. OPEN-ITEMS item 15.

## What happens

The new name rule (`services/component_names.py`) refuses control, format and
line-separator characters. It deliberately does NOT refuse a leading `=`, `+`,
`-` or `@`. Those are spreadsheet formula triggers, but `-` and `+` begin
legitimate data, and the rule is about what a name may contain, not about how a
sink renders it. So a component can be named
`=HYPERLINK("http://example.invalid","click")`. That works through the create
routes, and through any network file that arrives from someone else, which
opens permissively.

Reproduced on `6e56aae` plus the item 8 change, calling the two route helpers
directly:

```
accepted by the new rule: True
excel: sheet=buses cell=A3 data_type='f'
csv buses.csv line: "=HYPERLINK(""http://example.invalid"",""click"")"
```

- `GET /api/io/export/excel` (`routers/io.py::_export_excel_bytes`) goes through
  `n.export_to_excel`. openpyxl stores a string that starts with `=` as a
  FORMULA cell (`data_type='f'`), so the cell is live when the file is opened.
  The `AttributeError` fallback in the same function uses `ws.append` and does
  the same.
- `GET /api/io/export/csv` (`_export_csv_zip_bytes`, `n.export_to_csv_folder`)
  writes the name unescaped. A spreadsheet that opens the CSV evaluates it.

## Why it is a defect and not a choice

The assistant's own export tools already treat this as a hazard and guard it.
`chat_tools.export_to_excel` forces string type, and `chat_tools.export_to_csv`
prefixes an apostrophe (`_inert_csv_cell`). The IC workbook (`services/finance/
export_xlsx.py::_put`) does the same. The two network-export routes predate
those guards and never got them. The agent reported two more sites with the
same shape, which I have NOT reproduced: the profile templates in
`routers/network_profiles.py` (names as column headers via `df.to_excel`) and
the asset-results "About" sheet (`services/asset_results/export.py`).

## Severity

Low. It needs a hostile name in a network the victim exports, and the victim
must then open the file in a spreadsheet. Current Excel warns before following
an external link. It is the standard CSV/formula-injection shape, though, and
the product already decided elsewhere that it guards against it.

## Fix direction (not applied)

Guard at the sink, as the chat tools do. Reuse `_inert_csv_cell` for the CSV
export, and write names as explicit string cells in the Excel export. Refusing
the characters at ingress would not be enough: an existing project opens
permissively and can already hold such a name.
