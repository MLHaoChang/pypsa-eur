# Real-app click-through: Expert flow on the Data Center Energy Hub template (2026-09-27)

This run was headless Chromium against a local no-auth backend (`PYPSAGUI_LOCAL_MODE=1`) and the Vite dev server, on commit f934560.

**Verdict.** Every feature worked end to end:

- the template, network and VOLL;
- the study: 23 s, 11 of 30 solves, "Not certified", LOLE 12.38 h/yr [7.68–17.08] against a 3 h/yr target;
- the report, and the JSON and CSV exports;
- the prefilled review message;
- the FMEA preload and the B/C sweep;
- both working tours.

Reaching them needs hidden navigation, and the tagging tour is a dead end.

## First-time-user obstacles, ranked
1. **High — the study is hard to find.** It sits under Results → Adequacy, reached by scrolling the tab strip, in the sixth collapsed section, "REFERENCE DESIGN". Nothing leads there after template creation. (`AdequacyTab.tsx:111`)
2. **High — the template doesn't open.** After "create from template" the user stays on /projects and must press "Resume project", though the dialog promises "loaded immediately". (`NewProjectWizard.tsx:260-268`)
3. **High — the tagging tour is a dead end.** It launches from Results, where the Properties panel is hidden, and the EH bus fields exist only in Edit mode. (`EhReferenceDesignPanel.tsx:~807`, `cardKit.tsx:993-1071`)
4. **Medium — the report is off-screen.** It appears below the fold with no "finished" cue. The banner and readiness disappear while it runs. "Ask the assistant" scrolls the report out of view.
5. **Medium — a failed verdict gives no next step.** The guide's advice fits an inconclusive result only. The "ok" chips use the same red/accent colour as the failure text.
6. **Medium — jargon.** Examples: "decision 6", "P19 template", "Class-B", "MC hub boundary", "DtC", "ENS ‱", "SCR", lp_proxy/copt, "10.95 FOR", stage ids, and the DSR "double-count hazard" warning.
7. **Medium — the FMEA tab is off-screen.** It sits past the visible end of the Results tab strip.
8. **Low–medium — VOLL is hard to find.** It sits under Solver Settings → Network.
9. **Low–medium — Send stays enabled without an API key.** It stays enabled with no Anthropic key configured. (`ChatLaunchGreeting.tsx` and the dock's send gating)
10. **Low — tour popovers hide their target.** On steps 5 and 8 the popover covers the control it explains.
11. **Low — the template ships a warning.** It raises `gen_zero_costs` on 2 generators.
12. **Low — a misleading path.** The New-project dialog shows a save path that local mode does not use.

## Bugs / wrong states
1. **Raw numbers.** The DtC stress table shows "59558.514428810326" (`EhReferenceDesignPanel.tsx:~1614-1634`). FMEA Severity € is unformatted (`FmeaTab.tsx`).
2. **Contradictory solved state.** After the study and the sweep, the dock says "Solved — results match the network", while the canvas shows "Run a simulation to enable" and Obj "—".
3. **Live network changed after the study or sweep.** Every bus went from Control PQ to Slack, and Sub-net went from blank to 0/1/2. This contradicts "your network is never changed"; the likely cause is a topology recompute on the live network.
4. **A loaded bus shows "Total load 0 MW".** it_bus has a Load, but the Capacity summary reads 0 MW, probably because the load is held in time series.
5. **FMEA rows read as "nothing happened".** Class-A genset rows and both C scenarios show severity 0 with no explanation.
6. **A one-off 409 on resume.** It appeared after a backend restart with a stale tab open, from the identity guard; the app recovered.
7. **Console noise.** `GET /api/projects/unclaimed` returns a 404 in local mode and logs console.error.

Screenshots were kept in the session scratchpad (`realapp/shots/`) and are not committed.
