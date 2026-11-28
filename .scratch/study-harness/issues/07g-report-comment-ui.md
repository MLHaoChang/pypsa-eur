<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07g — Report comments in the report viewer: create and withdraw

Status: done (PR on branch claude/sh-07g-report-comment-ui; UX 34 plan GO WITH CHANGES and GK B plan GO, 2026-11-28)
Type: task
Blocked by: none (07d PR B merged as #1746 on 2026-11-20; PR A is #1649)
Gated: yes (UX; GK B for any route change)
Model tier: opus
Owner decisions: SH07C-DRAFT-1
Split from: [07d](07d-report-revision-drafts.md), PR B scope (coordinator, 2026-11-19)

## What to build

07c added report comments as routes only (`GET`/`POST …/reports/{report_id}/comments`, `…/withdraw`, `…/address`). The app has no screen for them. 07d PR B lists a run report's open comments in the run card and lets the user draft, confirm or discard a fix there, but a user still cannot write a comment from the app.

- In the report viewer, the user anchors a comment to a paragraph, bullet list or table (07c's `Anchor`) and writes its text.
- The user withdraws an open comment of their own.
- Each comment shows its status (open, addressed, withdrawn) and, when it is open with a reason, 07c's reason text.

## Acceptance criteria

- [x] A comment is created from the report viewer against the latest version's digest, through 07c's `POST …/comments`.
- [x] An open comment can be withdrawn; an addressed or withdrawn one cannot.
- [x] Refusals (`report_comment_closed`, `revision_stale_base`, the viewer role's 403) are shown in words.
- [x] UX placement agreed before building (the reports page is a workflow surface).
- [x] The chat disposition of `GET /api/projects/{name}/reports/{report_id}/comments` is settled: a typed read tool, or a limitation. 07d PR B holds it on this issue (`held-by-spec`).

## Likely files

`pypsa-gui/frontend/src/pages/reports/`, `pypsa-gui/frontend/src/api/reports.ts`, tests beside them

## Audit (2026-11-28)

- 2026-11-28 audit: 07d is `done (#1746)` and `components/runMonitor/RevisionDrafts.tsx` is on master, so the blocker is gone. `api/reports.ts` has `listReportComments` only; no create or withdraw call and no comment screen in `pages/reports/`. Left: everything in the acceptance list, including the chat disposition of the comments read (still `held-by-spec` on this issue). Order: UX placement (the reports page is a workflow surface), then the build. Model tier unchanged: opus.

## Build (2026-11-28)

- UI: `pages/reports/SectionComments.tsx` (one comments list per section under its header, "Comment…" in the section header on the latest version only, inline-confirmed Withdraw on the signed-in user's own open comments), words in `pages/reports/commentWords.ts`, wired from `ReportViewer.tsx` and `SectionCard.tsx`.
- Backend: `POST …/comments` takes an optional `base_digest`; a newer latest version refuses it `revision_stale_base` (409) under the report lock, and the UI always sends it.
- Chat disposition: a limitation. `report_comment_revisions` in `services/not_via_chat.py` is now `out of chat`, pointing to the report viewer; no typed read tool.
