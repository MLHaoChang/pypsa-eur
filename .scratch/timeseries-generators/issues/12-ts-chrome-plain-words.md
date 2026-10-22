# Time Series chrome: plain words

Status: needs-triage
Type: task
Blocked by: ts-05 (#586)
Gated: no; UX reviewer for the UI
Model tier: sonnet

Follow-up noted in the reviews of #586.

## Gap

The Time Series page's tab subtitles and sub-tabs name raw attributes ("p_set · MW", "p_max_pu × p_nom · MW", "p_min_pu", "marginal_cost"), its upload pane says "Values: p_max_pu in pu", and Guided's Assets "Open here" drawer reads "Generator '1 item(s)'" and stays on Loading. All predate ts-05 (seen in its screenshots).

## What to build

The editors' plain field words (`SeriesKit.fieldLabel`) in the tab chrome and the upload pane; the Guided drawer bug to its own owner (W3).

## Done when

No raw attribute name shows in Time Series' own text, and the Guided Assets drawer opens its item.
