# Grid-readiness check: a generic red-flag scorecard

Status: done (#442, c7cb3ca6)
Type: task
Blocked by: 01 (envelope), 04 (presets). An MVP can ship against defaults first.
Gated: yes (gridspine, adequacy)
Model tier: opus
Size: L

Owner decisions D2 (generic function, any template) and D3 (no TSO-specific profile).

## What to build

- One function, `grid_readiness(project, profile_id)`, that any template can call. It returns rows `{requirement, limit, source, project_value, verdict: pass|warn|fail|unknown, assumed: bool}`.
- Inputs it gathers:
  - grid-code profile limits (`grid_codes.yaml` or a published uploaded profile);
  - the SCR gate;
  - the energization schedule against peak demand;
  - island ride-through;
  - 01's dynamic envelope against ramp and oscillation limits;
  - 04's control presets (voltage and frequency support offered).
- A missing input gives `unknown`, never `pass`.
- UI: one scorecard panel. The report gets a chapter. The copilot gets a tool.

## Done when

The DC, microgrid and H2-hub templates each produce a scorecard, and each limit cites its source or reads "assumed".
