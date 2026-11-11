<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-43c — A malformed netCDF or MATPOWER import replaces the open network

Status: needs-triage
Type: bug

Found by LC-43 while covering `LC-D20.delivery_gates` ("malformed or oversized data"). The
manifest row `LC-D20.malformed_import` stays pending until this is fixed.

## What happens

The baseline project is open and saved, with an unsaved edit (G1 p_nom 20 → 33). A file is
uploaded and imported through the dispatcher:
`import_uploaded_network(file_id, format)`.

| File | Format | Result | Open project afterwards |
|---|---|---|---|
| `not a netcdf` | `netcdf` | tool_error, generic kind `tool_error` (an xarray `ValueError`; a direct call also hits `RuntimeError: no running event loop`) | **gone**: `project_uuid` None, empty network, the unsaved 33 MW lost |
| `function x` | `matpower` | **no error**: the import "succeeds" | **gone**: `project_uuid` None, empty network |
| `PK\x03\x04junk` | `csv_bundle` | tool_error `tool_error` | kept, G1 still 33 MW |
| `junk` | `excel` | tool_error `tool_error` | kept, G1 still 33 MW |

The project on disk is untouched. Only the in-memory session loses its project and its
unsaved edits.

## Done when

- A malformed file of any of the four formats is refused with a typed kind (for example
  `import_unreadable`).
- The open project, and its unsaved edits, stay as they were until a parsed network is ready
  to replace them.
- A test per format pins both points, and `LC-D20.malformed_import` in
  `pypsa-gui/backend/smoke/family_manifest.py` turns covered.
