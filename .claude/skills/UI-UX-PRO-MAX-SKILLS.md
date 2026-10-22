# UI UX Pro Max skill (vendored)

Source: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill, commit `076a2b3` (2026-10-22), skill version 2.15.0.
License: MIT, see `UI-UX-PRO-MAX-LICENSE`. REUSE coverage is the `REUSE.toml` annotation for `.claude/skills/ui-ux-pro-max/**`.

Installed: only the main skill `ui-ux-pro-max`. Not installed: the upstream plugin's six sibling skills
(`design`, `design-system`, `slides`, `brand`, `banner-design`, `ui-styling`).

## Role

A lookup table for implementers: `search.py "<query>" --domain ux|chart` and `--stack react|html-tailwind`.
It is python3 standard library only, makes no network calls and installs nothing.
`pypsa-gui/frontend/src/index.css`, `public/brand.css`, `docs/agents/review-ux.md` and the owner decisions
outrank it; see AGENTS.md "Agent skills".

## Local changes

- Copied from upstream `.claude/skills/ui-ux-pro-max/` (the `uipro init --ai claude` CLI was not run), so the
  11 `${CLAUDE_PLUGIN_ROOT}/.claude/skills/...` search commands in `SKILL.md` are rewritten to
  `python3 ".claude/skills/ui-ux-pro-max/scripts/search.py"`, the path `uipro init` renders.
- The description says "designing, building, or fixing interfaces"; upstream also says "reviewing", which
  would compete with the UX reviewer's own process.
- `scripts/tests/` is dropped (dev-only fixtures and tests).
- `.gitattributes` pins `data/**` to LF, because upstream's `validate_data.py` hashes those files.

## Updating

```bash
git clone --depth 1 https://github.com/nextlevelbuilder/ui-ux-pro-max-skill /tmp/uupm
# replace .claude/skills/ui-ux-pro-max/ with /tmp/uupm/.claude/skills/ui-ux-pro-max/,
# then re-apply the local changes above and bump the commit and version here.
```
