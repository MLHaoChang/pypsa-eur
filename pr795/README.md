Shots for #795 (Q3-CARDS-1 settings adapters, slice 2), head e1d87ecc7750ec62e46af78432a958ed96dced91 or later.
Produced without a chat model: the real `ConfirmationCard` frontend component, rendered by Vite and Playwright
chromium, fed the real card JSON that `harness.edit_card.preview(tool, args)` returns from the backend (a throwaway
pytest run modelled on tests/test_edit_card_{site,reports,library,campus}.py; the JSON is not committed). Pairs are
Expert then Guided (`useUIStore.setState({uiMode})`). The Expert `{}` box is the tool args, left empty here.

Shapes:
- 01 `set_finance_inputs`: two fields changed (Escalation capex, WACC).
- 02 `set_report_mapping`: untagged template plan (heading actions, renamed text, section mapping).
- 03 `put_library_item`: tariff new version (v1 to v2, jurisdiction, description).
- 04 `campus_set_island_config`: four island settings changed. Expert is rendered 700 px wide; at 460 px the
  Unit column is cut off.
- 05 `set_finance_inputs` first save: capped card, 12 rows plus the "10 more finance inputs change." note.
