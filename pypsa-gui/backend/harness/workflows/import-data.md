---
id: import-data
title: Import a network or data
intent: Bring in a network file (netCDF, CSV bundle, Excel, MATPOWER) or time-series profiles, and check what arrived.
when: [expert, guided]
order: 30
opening_request: I want to import a network or data files. Tell me which formats you accept and ask me what I have.
steps:
  - id: format
    title: Pick the format
    done_when: The user has said which file kind they have and it is attached.
  - id: import
    title: Import
    done_when: The import tool returned without error.
  - id: verify
    title: Verify what arrived
    done_when: The user has seen counts, the snapshot range and any validation warning.
---

## Step: format

Ask with `ask_user`: a whole network (netCDF `.nc`, a PyPSA CSV bundle
`.zip`, an Excel workbook, a MATPOWER `.m` case) or time series for existing
components (load, generator or link profiles as CSV). Mention that the file
goes in through the attachment button. `list_uploads` shows what is already
attached; `download_timeseries_template` gives the user the CSV shape.

## Step: import

Networks: `import_network_nc`, `import_csv_bundle`, `import_excel` or
`import_matpower` with the upload. Profiles: `upload_load_profile`,
`upload_generator_profile` or `upload_link_profile`. A network import
replaces the resident network and goes through a confirmation card; say so
before calling it. For an Excel workbook that is not a PyPSA export, read it
with `read_excel_sheet` first and propose a mapping.

Equipment tables (one row per existing asset, one class per table, e.g. line
ratings or battery costs): read the sheet with `read_excel_sheet`, take field
names and units from `get_component_schema`, then call
`preview_equipment_mapping` with the column → {field, unit} mapping. Cost
columns need the table's currency and money year; ask, never assume. Show the
user the unmapped cells with their reasons; do not guess a field, unit or
asset for them. When the user agrees, call `apply_equipment_mapping` with the
same arguments and the preview's source digest; the edit card lists every
changed cell with its source cell.

## Step: verify

Call `get_meta`, `list_snapshots` and `validate_network`. Report counts,
the snapshot range and every warning with its component name. Offer
`save_project` so the import is not lost, and the Build-a-network workflow
for anything missing.
