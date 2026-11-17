# Route chat-disposition fragments

A pull request that adds a route gives it one chat disposition by adding one
file here, `<branch>.json` (as in `doc/release-notes.d`), instead of a row in
`tests/test_route_chat_disposition.py`. So two pull requests never edit the
same lines (RETRO-FRAG-1). Changing or deleting an existing row stays an edit
where that row lives. A route a chat tool calls over HTTP still goes in that
tool's `TOOL_ROUTES` entry, and one chat refuses goes in a
`services/not_via_chat.d/` fragment.

```json
{
 "served_by_tool": {"GET /api/projects/{name}/thing": "get_thing"},
 "ui_internal": {"thing_editor": {"reason": "one line: why only the app's own screen calls it",
                                  "routes": ["PUT /api/projects/{name}/thing"]}},
 "known_gaps": {"GET /api/projects/{name}/thing.csv": "Results panel"}
}
```

Every section is optional. A route key is `"METHOD /path"` exactly as FastAPI
mounts it. The checks run on the merged tables: a row the test file or another
fragment already defines, a route that no longer exists, a tool that does not
exist, and a known gap past `_KNOWN_GAPS_CEILING` all fail. `known_gaps` only
shrinks, so a new gap also raises the ceiling and needs a reviewer's reason;
declare a route without a tool as "unbuilt" in `services/not_via_chat.d/`
instead. A gap a tool now serves is listed as stale, and the coordinator's
sweep (`python -m smoke.prune_stale_declarations`) deletes it from its file.
