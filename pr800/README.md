Shots for #800 (LV08 B-campus, GRIDCODE-CHAT-1 cards), head afb027aa1eb26eb2237c3b93c139654b8554271b.
Produced without a chat model: the real `ConfirmationCard` frontend component, rendered by Vite and Playwright
chromium, fed the real card JSON that `harness.edit_card.preview(tool, args)` returns from the backend on a hub
project with real grid-code files (a throwaway pytest run on the `codes` fixture of
tests/test_lv08_bcampus_tools.py; the JSON is not committed). Each shape is shot in Expert (700 px) and Guided (460 px).
Expert's `{}` box is the tool args.

Shapes:
- 01 `campus_confirm_grid_code_limit`: Voltage band 1, quote found on its page.
- 02 `campus_confirm_grid_code_limit`: Reactive range at the PCC, quote not found on its page (it is on page 5).
- 03 `campus_publish_grid_code`: replaces the published profile, with two unconfirmed limits allowed (they stay extracted).
- 04 `campus_publish_grid_code`: the same without allow_unconfirmed (the notes say publishing is refused until confirmed).
- 05 `campus_delete_grid_code_draft`: the draft “TSO code”.
- 06 `campus_delete_grid_code_document`: code.pdf, cited by one draft.
- 07 `campus_start_milp`: k 4, margin 25 %; the one change is the job, its settings are a note in the run panel's words.
