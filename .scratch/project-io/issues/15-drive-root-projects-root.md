<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 15 A drive-root `projects_root` refuses every project name

Status: needs-triage
Blocked by: none
Source: GK B 69's nit on #1798 (excel-input-workbook S2).

## Problem

A drive-root `projects_root` (`C:\` or `/`) makes `storage_paths.allocate_storage_path` refuse every name, because the prefix check is `root + os.sep`. `os.path.realpath` of a drive root already ends in the separator, so the check compares against `C:\\` or `//`, which no project folder starts with, and every create is refused.

## Acceptance criteria

- [ ] A project can be created under a drive-root `projects_root` (`/` on POSIX, `C:\` on Windows), and a path that escapes the root is still refused.
- [ ] A test pins both, for example with `os.path.commonpath` or by stripping a trailing separator before the check.

## Likely files

- `pypsa-gui/backend/services/storage_paths.py` (`allocate_storage_path`, the `full.startswith(resolved_root + os.sep)` check)
- its tests
