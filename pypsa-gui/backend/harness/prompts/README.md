# Prompt fragments

The system prompt is assembled by `services/chat_service._build_system_prompt`
from the fragments in this folder, in this order: base_identity (+ the
confirmation_card_contract when tools are on) + style_guidance,
assistant_stance, the profile-awareness block (built per turn), domain_guide,
solver_error_decoder, price_congestion_guide, next_step_rubric,
adequacy_guide, eh_guide, untrusted_data_clause, then the live network meta.

## The doctrine the files follow

- **Byte-identical.** The assembled default prompt is cached by prefix and
  pinned by hash. Changing a word here changes every user's prompt and
  invalidates the cache; do it on purpose, with the hash tests updated in the
  same change and the reason in the commit message.
- **facts / chaining.** Each guide has a FACTS half (definitions, ranges,
  fidelities — true with no tools offered) and a CHAINING half (which tools
  to call, in what order). A tools-less profile gets FACTS only; the rule is
  that the tools-off prompt names NO tool. `full` = facts + chaining where
  the halves concatenate to the original literal; `solver_error_decoder` and
  `next_step_rubric` keep a separate `full` because the original puts the
  tool imperative in the middle, and their halves are kept word-multiset-
  equal to it by test_solver_and_rubric_halves_cover_the_same_words.
- **New guidance is a new fragment, not an edit of a pinned one.** That is
  how adequacy_guide and eh_guide arrived, and how the skill catalogue block
  (issue 05) will.
- **trailing_space** in the front matter lists the sections that end with a
  space, so a FACTS half followed by its CHAINING half needs no join. The
  loader reflows everything else: wrap the files for reading, never for
  meaning.
- **Templates.** `confirmation_card_contract` carries `{session6}`;
  `untrusted_data_clause` carries `{open}` / `{close}`. chat_service formats
  them; nothing else in a fragment is a brace.
