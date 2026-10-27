Shots for #795 (Q3-CARDS-1 settings adapters, slice 2), head 90873a415ee43c86b3af8ce37792ac48a693e91a or later.
Produced without a chat model: the real `ConfirmationCard` frontend component, rendered by Vite and Playwright
chromium, fed the real card JSON that `harness.edit_card.preview(tool, args)` returns from the backend (a throwaway
pytest run modelled on tests/test_edit_card_{site,reports,library,campus}.py; the JSON is not committed). Pairs are
Expert then Guided (`useUIStore.setState({uiMode})`). The Expert `{}` box is the tool args, left empty here.

Shapes:
- 01 `set_finance_inputs`: two fields changed (Escalation capex, WACC).
- 02 `set_report_mapping`: untagged template plan (heading actions, renamed text, section mapping).
- 03 `put_library_item`: tariff new version (v1 to v2, jurisdiction, description).
- 04 `campus_set_island_config`: five rows changed (Droop pct, Sustained ride-through and its source, Rate-of-change window in ms, Scenarios as UPS-bridged, Seamless to Seamless). Expert is rendered 700 px wide so the Unit column is not clipped; Guided is 460 px.
- 05 `set_finance_inputs` first save: capped card, 12 rows plus the "10 more finance inputs change." note.
