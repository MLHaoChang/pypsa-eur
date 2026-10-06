# Glossary Map

## Contexts

- [pypsa-gui](./pypsa-gui/CONTEXT.md): the React + FastAPI application layered over PyPSA, with its own vocabulary (Project, Scenario, Context, ...). Its decisions live in `pypsa-gui/docs/adr/`.
- gridspine (`gridspine/`): no glossary yet.
- PyPSA-Eur workflow (`scripts/`, `rules/`, `config/`): no glossary yet; upstream PyPSA-Eur terminology applies.

## Relationships

- **pypsa-gui → gridspine**: the GUI backend imports gridspine (for example `pypsa-gui/backend/main.py`, `services/solve_queue.py`).
