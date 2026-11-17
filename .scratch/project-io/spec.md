# Project I/O: bundles, a self-contained folder, model imports

Owner decisions: IMPORTS-1, IMPORT-FLOW-1, PROJ-SELF-1, AI-IMPORT-1, HOLISTIC-1, EXPERT-GUIDED-1, RAW-NAMES-1, PR-SIZE-1.
Builder: project-io core (session_01G7qCumV3StLEo5RMZgP3rd). Sibling importers: Excel workbook, PSS/E, PowerFactory DGS, PLEXOS.

1. Shared import contract `gridspine/ingest/import_result.py` (#1600).
2. Bundle integrity: one table of what a project is, used by export, Save-As, fork and snapshot; a refused import leaves nothing behind; a manifest with schema version and checksums; an import dry run (issues 01, 02).
3. Self-contained folder: pinned library items and used weather slices copied into the project; a "newer version in the library" notice; migration (issue 03).
4. Import pipeline: one route and chat tool, a preview in plain names, then a new project; the existing raw imports go through it (issue 04).
5. Assistant suggestions in the preview (AI-IMPORT-1) (issue 05).
