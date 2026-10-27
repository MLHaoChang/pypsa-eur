# GK B conditions and riders, verbatim (received 2026-10-26 23:36Z from session_01WQthJT)

Sources: (1)–(6) from 011aHPZq → 01V7mmK4 at 18:51Z. Riders (1)–(5) from 01VH95 → 014skEWe at 22:35Z. R1–R4 from 01VH95 → 01NEaJ9t at 22:42Z/22:43Z.

CONDITIONS FOR PHASE 2:
1. The before/after table in the PR body covers every template (IPP included), redundancy and the guided study. Template LCOE/NPV/annual cost must be exactly equal; the study's bills come out ×1.257335 within rounding. The owner checks the deltas before merge (the standing money-year 01 rule); the coordinator asks.
2. The conversion: I checked the factor, 1.029·1.092·1.064·1.026·1.025 = 1.257335, and de 0.11 → 0.138307 is consistent. In the pack, record the index source (Eurostat prc_hicp_aind, EEA, the technology-data v0.14.0 vendored file) and the factor beside the converted rows. Store the values at the precision given, not rounded further.
3. The old versions stay byte-identical: 2026-10-22, 2026-10-05, 2026-10-25 and the regional 10-25+ ones. A project pinned to an old version reloads with the same values, hash and case year 2020, as in your test list.
4. No repin beyond P1 to P5, the study and the regional rebuilds. As #713's plan condition requires, this is LG-02's own repin. List every PACK_VERSION constant that moves in the PR body.
5. The shipped network.nc and sidecars are regenerated through _build.py only. A test or check shows that the LP numbers are unchanged and only the stamps differ.
6. currency_year_mixed still refuses a 2020 tariff against 2025 rows, with a test. The tripwire stays green with _ACKNOWLEDGED_BUMP_CHANGES's acknowledgement removed. Add a release fragment.
(Same message, phase-1 calls also in force: P5, eh_ipp.PACK_VERSION moves with P1–P4. Each template's tariff ledger/notes says "synthetic, illustrative; no money year of its own; read in the case's money (2025)". CO2 stays HOLD 2020.)

RIDERS, Q1 (a):
1. Store the factor and the five HICP rates as strings, or exact decimals, in the manifest `money_year_conversion`. Store the converted tariff values once, at full float precision, and never recompute them at load time. The rates' product must reproduce the factor exactly: add a test that recomputes it from the five stored rates and asserts equality to the stored factor.
2. In the before/after table, compute each study-bill ratio from the run outputs. Assert ratio == factor at rtol 1e-9, on a fixed dispatch. Re-sized runs are listed separately as "moved by re-optimisation", not as ×factor.
RIDERS, Q2 (a):
3. Pin `tests/golden/site_fixture.py:69` to `load_defaults("2026-10-05")`, with a one-line reason that names the frozen WP0 parity. Pin only that fixture: no other test may change its pack argument without a line in the PR body. List each one that does.
4. Add at least one test that runs the guided study on the live pin (2026-10-26) end to end and asserts the pack hash, currency year 2025 and the ×factor bill. Without it, the golden pin would let a live-pack regression through unseen.
5. The u2_* fixture files must stay byte-identical. State that in the PR body; `git diff --stat` on them should be empty.

What has moved on since:
(a) Rider 2 overrides condition 1's "within rounding". The exact factor is 1.2573346011408 (unrounded products), at rtol 1e-9.
(b) Condition 3 now also covers stored studies on the old pin running without currency_year_mixed. That is plan A with C1–C5 (the reset_rows fix included).
(c) If site_fixture switches to the WP0_PACK_VERSION constant (plan A), it still counts as rider 3's pin. Keep its one-line reason.

PLAN A changes (GK B 23:33Z): C1 reset_rows on stored ledger's library (after reseed if same request); C2 strict parse, LibraryError names ledger_version, _library_for_or_500 → 500 naming reason, no new cache; C3 docstring: LIBRARY_VERSION→10-05 is what master did; test a LIBRARY_VERSION stored ledger runs without currency_year_mixed; C4 same-bill vs value computed on 10-05, rtol 1e-9; C5 PR body classifies all 16 load_defaults callers.

R1–R4, verbatim (GK B session_01VH95FbpjNzxVG6BqcVzcjZ → session_01NEaJ9tPn9bhwxGrvA3cgDv, 2026-10-26 22:42:47Z; "option (i), list them in this PR"):
R1. One literal key per row in `_PACK_ROWS_NOT_SEEDED`, with no prefix or wildcard match, because a key is a contract. A script can generate the list, but commit it as literal entries. Reasons are short category tokens like the existing one (e.g. "campus_only", "heat_kit_only", "refinery_only"), with a comment line per category.
R2. Stale guard: a test that every key in `_PACK_ROWS_NOT_SEEDED` exists in the live pack, so no entry outlives its row. If the load already refuses unknown keys, name that test instead.
R3. No hidden seed gap: the PR body says that no listed row is a technology the guided ledger already maps under another key (a newer PV or battery cost row, for example). For each listed row whose technology appears in `_PACK_TECH_ROWS`, either seed it or say why the old row stays the source.
R4. Condition (3) still applies: old pinned studies load byte-identically. Adding entries to this dict must not change `ledger_hash` or the pack hash of the 2026-10-05 load, so test it there.
