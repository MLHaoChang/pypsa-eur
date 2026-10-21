# Coding standards

Rules a reviewer applies to every diff (`mp-code-review` reads this file). A violation is a review finding.

## UI

1. **The server says what can run or will be computed.** The UI shows what can run, what is blocked and what will be computed from the server's `/flow` verdict only. A frontend availability table, or any client-side rule that decides it, is a finding: fix the server's verdict, and have the UI render it.
2. **No internal ids in user-facing copy.** Words the user reads carry no section signs (`§`), workstream or question ids (`W3a`, `Q17`), ticket ids (`LC-12`, `DC-5`, `GS-`) and no raw PyPSA class path (`Link › grid_import`). Name the thing in words. Enforced by the UI copy guard, `pypsa-gui/frontend/src/uiCopyGuard.test.ts`; an intended word goes in its `ALLOW` list with the reason.
