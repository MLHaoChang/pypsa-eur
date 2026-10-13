# One investment engine; Guided mode compiles onto it

Two efforts built tariffs, bills, cash flows and an assumptions library twice: the Edge Investment
Case (`services/commercial`, `services/library`, `services/finance`) and the Guided investment study
(`services/study`). We keep one engine, the Investment Case's, and the Decision study keeps only its
question flow, Assumptions ledger, Options, Verdict and report. It turns its Key parameters and
Generic defaults into the engine's own inputs rather than computing anything itself. Two engines would
give two answers on the same Project and two places to fix every bug. The Investment Case engine is
the deeper of the two (15-minute billing, contracts, debt, tax, SAM-oracle parity).

## Consequences

- The engine stays strict: an input nobody supplied is Unavailable ([ADR-0001](0001-unresolvable-figures-ship-as-null.md)).
  Guided mode is complete because every value it sends is explicit, either from the user or from a
  Generic default that it lists in the report.
- A Decision study opened in Expert mode shows the engine's full case, defaults included; an expert
  edit there marks the matching Assumptions ledger row as changed.
- Plan and phasing: `docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md`.
