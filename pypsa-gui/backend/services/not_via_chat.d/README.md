# Not-via-chat fragments

A pull request that declares a feature chat cannot do (a route chat must
refuse or point elsewhere, or one still "unbuilt") adds one file here,
`<branch>.json` (as in `doc/release-notes.d`), instead of an entry in
`services/not_via_chat.py`. So two pull requests never edit the same lines
(RETRO-FRAG-1). Changing or deleting an existing entry stays an edit where it
lives. The app reads this folder at import, and `pypsa-gui.spec` ships it.

```json
{
 "entries": [{
  "id": "thing_editor",
  "title": "Thing editor",
  "disposition": "manual",
  "reason": "One or two sentences, 40 to 200 characters: why chat cannot do it and where the user does it.",
  "page": "Thing page (/thing)",
  "panel": null,
  "keywords": ["thing", "things"],
  "routes": [["PUT", "/api/projects/{name}/thing"]]
 }],
 "goals": {"Edit my thing": "thing_editor"}
}
```

Both sections are optional. `disposition` is one of `DISPOSITIONS`; `panel` is
null or a safety-panel id; keywords are lower-case words outside the test's
`BANNED` list. Each `goals` line is a user ask that `find_capabilities` must
answer with that entry first; `tests/test_registry_fragments.py` asks it.

Entries keep their order: `NOT_VIA_CHAT` in the module, then
`not_via_chat.json`, then each fragment in file-name order, and `matches` breaks
a tie in hit count by that order. A malformed fragment, an id already declared,
or a goal naming an unknown entry fails the import, so the app never runs on a
partial table; `tests/test_not_via_chat.py` and the route-disposition test then
check every entry as they check the module's.
