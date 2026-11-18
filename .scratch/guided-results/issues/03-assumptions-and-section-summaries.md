# Collapsed, grouped Assumptions card; summaries in sections 2, 4, 5, 6

Status: ready-for-agent
Type: task
Spec: `.scratch/guided-results/spec.md` §2, §3

Gated: GK B (62), UX (23). GK B condition G4 in `01-headline-card.md` applies.

## Comments

### Plan verdicts (2026-11-17)

GK B (62): plan GO (last ruling; GK B 63 reviews). Helpers stay in the asset count, said as "incl. N helpers".
- (1) `helper` is a server function shared with `is_slack_name` and `generator_role`; never a copied prefix list. Test each prefix and role, and a plain generator.
- (2) A missing or non-bool `helper` reads as not a helper, never crashes.
- (3) A test pins that no state, count, verdict, ack or fingerprint reads `helper` or the sums (H2 parity, plus a frontend spy).
- (4) One chart per real unit; tonne buses never on the MW axis; mixed units listed, never added; the cap note on both the line and the chart when `holds_total > holds.length`.
- (5) The Assumptions collapse changes nothing acknowledged: the ack payload is byte-equal before and after.

UX (23): plan GO with conditions.
- (a) "·" between fields, commas inside one field: "3 carriers: electricity, heat, hydrogen · 1 process unit". The owning panels' words ("load profiles", "availability") with a parity test against the word map. Sums only of the same field and unit. The demo-data label stays visible where a total uses demo data (#1174). An empty section shows its plain state and the existing add link, never "0 assets · 0 MW fixed". The line never contradicts the state dot.
- (b) Section 5 chart: no chart for one carrier or no fixed capacity (the line says so); a text alternative with the same numbers; the PR says carrier tokens differ from the canvas colours until THEME-LIGHT (c) merges; unit on the axis; a tonne-bus carrier gets its own chart; helpers named in a visible line.
- (c) "▸ Assumed · 5 not yet confirmed" + "Keep all defaults"; when the section waits on them the line says so in the warn style; the count equals Readiness's and Expert's Assumed chips (test over a template); open/closed is a per-user convenience, default collapsed.
- Shots: dark and light, sections 2, 4, 5, 6 on dc- and mg-; an empty section; the hydrogen-hub template with a tonne bus; the card collapsed, open and waiting; one Expert shot with the same counts.
