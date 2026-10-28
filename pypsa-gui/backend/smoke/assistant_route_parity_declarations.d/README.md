<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Route-parity declaration fragments

Add new route-parity declarations here, not in
`../assistant_route_parity_declarations.json`. One file per pull request, named
after the branch (`claude/foo` becomes `claude-foo.json`), using the base file's
list keys (`service_matches`, `limitations`, `needs_tool`, `non_mutating_routes`,
`binding_overrides`, `acknowledged_shadows`):

```json
{"needs_tool": [{"method": "GET", "path": "/api/example", "family": "projects"}]}
```

New files never conflict, so pull requests merge in either order. When a tool
starts serving a declared route, leave the declaration: `--check` lists it as
stale and passes, and the coordinator's sweep deletes it
(`smoke/prune_stale_declarations.py`). A duplicate item, an unknown key or a
malformed file fails `assistant_route_parity.py --check` and names the file.
