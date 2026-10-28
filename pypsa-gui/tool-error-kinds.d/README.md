<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Tool-error kind fragments

Add a new `error_kind` here, not in `../tool-error-kinds.json`. One file per pull
request, named after the branch (`claude/foo` becomes `claude-foo.json`):

```json
{"kinds": {"my_new_kind": {"surface": "inline", "why": "Which tool refuses what, and what the assistant does next."}}}
```

New files never conflict, so pull requests that add kinds merge in either order.
Change or delete an existing kind where it is defined (the base file or its
fragment). The tests read the base plus every `*.json` here, in file-name order
(`pypsa-gui/backend/smoke/json_fragments.py`); a kind defined twice, an unknown
key or a malformed file fails them and names the file.
