# 05. `tariff_payload_resolved` does not show a stamped tariff's currency

Status: ready-for-agent
Type: task
Raised by: GLE-03 session (issue 03), repeated by GLE-04 (issue 04, PR #1290).
Gated: yes (money semantics).

## What is missing

`models.commercial.tariff_payload_resolved` shows a tariff's stated currency only. A copied `generic` pack tariff states none, so its resolved payload shows no currency even though the currency gate and the DC energy-price check now read EUR from its `pack_hash` stamp (`binding.resolved_tariff_currency`).

## What a fix would need

- The resolved payload shows the resolved currency and says it came from the stamp, using `resolved_tariff_currency`. Nothing is written onto the tariff, so hashes stay as they are.
- A test: the copied generic tariff's resolved payload shows EUR from the stamp; a tariff that states a currency shows that one.

## Pointers

- `pypsa-gui/backend/models/commercial.py` (`tariff_payload_resolved`).
- `pypsa-gui/backend/services/commercial/binding.py` (`resolved_tariff_currency`).

## Comments

GK A plan GO with conditions (2026-11-10):

- **G5-1 Round trip writes nothing** (option 1, replacing the marker-only strip). A read adds `currency_source: "stamp"`. A write drops that key, and drops `currency` only while it still equals the stamp's. A differing currency is stated content, and so is an explicit currency with no marker. GET then PUT `/simulation/solver_config` (the frontend's `saveCommercial`), and a Library item edit, keep the stored bytes, the library digest and hash v1/v2 unchanged. Shown red before the fix. A marked write whose pack cannot be read is a typed refusal (`tariff_stamp_unreadable`, a 422 naming the pack), never a 500 and never stored with the currency. A RuntimeError propagates.
- **G5-2 Broken pack on the read.** A GET never 500s on the pack's own error class (`DefaultsPackError`). It returns the payload with no currency and `currency_source: "pack_unreadable"`. Anything else propagates. Binding keeps raising (GLE3-1), so a run still refuses.
- **G5-3 Every reader.** Both route readers (`routers/library.py` item GET, `routers/simulation.py` GET `/solver_config`) and the function are tested for stamp, stated, implied and generic-no-stamp.
- **G5-4 No-write proof.** The input dict stays deep-equal, and the canonical hash and recipes 1/2 stay equal, plus G5-1's route round trip.

Note: a Library tariff write refuses unknown keys, so `items.validate_payload` lets the read-only `currency_source` through for a tariff. A legacy stamped Library item (stored before rule 01) that is read and put back is refused with `tariff_currency_required`, and no version is stored.

GK A option-1 conditions (2026-11-10), after `test_models_do_not_import_services` ruled out any services import in models:

- The read lives in `services.commercial.binding.tariff_payload_read`. `models.commercial.tariff_payload_resolved` is as on master. Both route readers call the service function, and no other caller needed the stamp.
- The write strip in `Tariff` reads a lookup slot. `services.commercial.stamp_currency` registers it when imported (by `services.library.items`), and `main` registers it explicitly at startup. A marked write with no lookup registered is refused `tariff_stamp_unreadable`. A test imports `main` and asserts the slot is set. The only test that swaps the slot does it in its own `MonkeyPatch.context()` and shows that an unmarked write is unaffected. The loader module is left alone (live BC-02 area), so the import of `items` plus the explicit call in `main` cover registration.
- `tariff_stamp_unreadable` is a new error kind (`tool-error-kinds.d` fragment). The Library route answers 422 with `code` set to it, and PUT `/solver_config` answers FastAPI's 422 with that error `type`. Neither is ever a 500.
- Mutants: dropping the equality compare (strip on the marker alone) and dropping the no-lookup refusal must each be killed.
