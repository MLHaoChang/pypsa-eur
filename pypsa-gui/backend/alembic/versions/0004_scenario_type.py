"""scenario_type

`Project.scenario_type` becomes a real column, and the `[type]` prefix that
carried it inside `scenario_description` is lifted out of existing rows.

**What the prefix was.** The scenario-create dialog wrote
`"[stress] cold winter, no imports"` into `scenario_description`, and exactly
one panel knew to strip it back off. Every other surface that rendered a
description printed the marker at the user — a scenario saved with no
description of its own displayed the literal text "[scenario]" — and the
category could not be corrected after creation without the user editing
around a piece of syntax nobody told them about.

**The backfill is the point.** Adding the column alone would leave every
existing scenario categorised NULL while its description still carried the
tag, so the UI would show no badge AND the raw marker: strictly worse than
before. This migration parses each description, moves the category into the
column, and rewrites the description without it.

**Only the three known values are recognised.** A description that merely
opens with a bracket — "[draft] cut the gas fleet" — is left completely
alone, description and all. The rewrite must never eat a user's first word,
and a bracketed word that is not one of ours is prose, not an encoding.

**Idempotent.** Re-running matches nothing, because the descriptions no
longer start with a recognised tag. The migration's own idempotence test runs
it twice.

Revision ID: 0004_scenario_type
Revises: 0003_relative_storage_path
Create Date: 2026-08-04 00:00:00.000000
"""

import logging
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_scenario_type"
down_revision: str | None = "0003_relative_storage_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOG = logging.getLogger("alembic.runtime.migration")

# The three values the dialog ever wrote. Spelled out rather than `\w+` so a
# description opening with any other bracketed word survives untouched.
_TAG_RE = re.compile(r"^\[(baseline|scenario|stress)\]\s*([\s\S]*)$")

# Untyped columns on purpose — see 0003's note: declaring `id` as `sa.Uuid`
# makes SQLAlchemy re-encode it as 32 undashed hex on the way into the WHERE
# clause, while 0001 stored the dashed form, so every UPDATE matches zero rows
# and the migration silently does nothing.
_PROJECTS = sa.table(
    "projects",
    sa.column("id"),
    sa.column("scenario_description"),
    sa.column("scenario_type"),
)


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("scenario_type", sa.String(length=32), nullable=True)
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.select(_PROJECTS.c.id, _PROJECTS.c.scenario_description)
    ).all()

    changed = 0
    for row_id, description in rows:
        if not description:
            continue
        match = _TAG_RE.match(description)
        if match is None:
            continue                                  # prose, or already lifted
        scen_type, remainder = match.group(1), match.group(2).strip()
        changed += 1
        conn.execute(
            _PROJECTS.update()
            .where(sa.cast(_PROJECTS.c.id, sa.Text) == str(row_id))
            .values(
                scenario_type=scen_type,
                # '' -> NULL: an empty description and a missing one are the
                # same thing to every reader, and "[scenario]" alone was by far
                # the most common stored value — the dialog wrote the tag even
                # when the user typed nothing.
                scenario_description=remainder or None,
            )
        )

    # Visible, per 0003's lesson: "lifted 0 of 400" is the signal that the
    # descriptions were not the shape this migration expected, and it is
    # otherwise indistinguishable from a database that had none to lift.
    _LOG.info("0004 upgrade: lifted %d of %d project description tag(s)", changed, len(rows))


def downgrade() -> None:
    """
    Re-prefix, then drop the column.

    Order is load-bearing in the mirror of upgrade's: the descriptions must be
    rewritten while `scenario_type` still exists to read from. Dropping first
    loses every category irrecoverably — the rollback would "succeed" and
    silently discard data the upgrade was careful to preserve.
    """
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(_PROJECTS.c.id, _PROJECTS.c.scenario_description, _PROJECTS.c.scenario_type)
    ).all()

    restored = 0
    for row_id, description, scen_type in rows:
        # Only the values the prefix encoding could represent. A category added
        # later has nowhere to go in the old format; dropping it is the honest
        # outcome, and mangling the description to smuggle it is not.
        if scen_type not in ("baseline", "scenario", "stress"):
            continue
        restored += 1
        conn.execute(
            _PROJECTS.update()
            .where(sa.cast(_PROJECTS.c.id, sa.Text) == str(row_id))
            .values(scenario_description=f"[{scen_type}] {description or ''}".strip())
        )

    _LOG.info("0004 downgrade: re-prefixed %d of %d project description(s)", restored, len(rows))
    op.drop_column("projects", "scenario_type")
