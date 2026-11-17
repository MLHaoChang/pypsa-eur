# Lock-policy fragments

A pull request that adds a write route or a chat tool needing a lock decision
adds one file here, `<branch>.json` (as in `doc/release-notes.d`), instead of a
row in `tests/test_write_surface_lock_policy.py`. So two pull requests never
edit the same lines (RETRO-FRAG-1). Changing or deleting an existing row stays
an edit where that row lives.

```json
{
 "routes": {"POST /api/projects/{name}/thing": {"category": "not-project-data", "reason": "why no lock applies"}},
 "tools": {"my_tool": {"category": "chat-session", "reason": "why no lock applies"}},
 "known_gaps": {"my_tool": "the recorded, reproduced defect"}
}
```

Every section is optional. A route key is `"METHOD /path"` exactly as FastAPI
mounts it; `category` is one of the test's `CATEGORIES`. The checks run on the
merged tables: a row the test file or another fragment already defines, an
unknown section or category, a route that no longer exists and a gap that is
fixed all fail, naming the file.
