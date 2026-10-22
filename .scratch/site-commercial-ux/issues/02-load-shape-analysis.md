# Load Shape Analysis tab

Status: done (#440, 59b2f92f)
Type: task
Blocked by: none (it can share the flows helper from 01 if that lands first)
Gated: no
Model tier: sonnet
Size: M

## What to build

A results tab with:
- import and export heatmaps, weekday × hour, averaged over a chosen period;
- a profile snapshot: one week, with a week picker, showing grid, solar, wind, EV, genset, flexible load and battery SoC, plus flagged intervals and unserved load;
- monthly site energy, stacked (solar, grid, export, excess), with a daily-generation line and its average;
- an annual site energy stack, with a year picker.

All of it is computed from existing solved results; no new solve.

## Done when

It renders on the data-centre, microgrid and H2-hub templates, Guided mode shows it without Expert-only controls, and the tab is registered so the copilot can navigate to it. That registration lands late, after #408 and #416, and needs the coordinator's go before touching `harness/catalogue.py`.
