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

**The BACKFILL is idempotent; `upgrade()` as a whole is not.** Re-running the
rewrite matches nothing, because the descriptions no longer start with a
recognised tag — that is the half that could corrupt data, and it is safe.
Calling `upgrade()` twice still fails on `add_column` with "duplicate column
name", which alembic never does on its own but which matters to anyone
hand-repairing a half-applied revision: drop the column first, or stamp.

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

# The API's own cap on a description (`schemas.py`), and the declared width of
# `projects.scenario_description` (0001). Both are 500; the downgrade needs the
# number because re-prefixing can push a description past it.
_DESCRIPTION_LIMIT = 500

# Untyped columns on purpose. Declaring `id` as `sa.Uuid` makes SQLAlchemy
# decode the stored value into a `uuid.UUID` on the way OUT, and `str()` of
# that is the DASHED form — while the column holds 32 undashed hex characters
# (`CHAR(32)`, per 0001). Compared as text, dashed never matches undashed, so
# every UPDATE affects zero rows and the migration silently does nothing.
#
# Verified against the live database rather than assumed: reading through a
# typed column yields `UUID('47267f9a-f8e4-…')` where the stored bytes are
# `47267f9af8e4…`, and the typed UPDATE reports rowcount 0 against the
# untyped one's 1. (0003's note and an earlier draft of this one both had the
# direction backwards — they said 0001 stored the dashed form. The conclusion
# was right and the stated mechanism was not, which is worse than no comment
# for whoever writes 0005.)
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
    skipped_too_long = 0
    for row_id, description, scen_type in rows:
        # Only the values the prefix encoding could represent. A category added
        # later has nowhere to go in the old format; dropping it is the honest
        # outcome, and mangling the description to smuggle it is not.
        if scen_type not in ("baseline", "scenario", "stress"):
            continue
        prefixed = f"[{scen_type}] {description or ''}".strip()
        # The prefix adds up to 11 characters to a column declared
        # String(500) — and 500 is exactly the API's own cap, so a description
        # written at the limit overflows it. SQLite does not enforce length and
        # would take it silently; Postgres aborts the whole downgrade with
        # "value too long for type character varying(500)", leaving the
        # database stranded between two revisions during a rollback, which is
        # the worst possible moment.
        #
        # Dropping the category for these rows rather than truncating: the
        # description is the user's text and the category is recoverable by
        # re-labelling, so losing 11 characters of prose to preserve a value
        # the old format can hold anyway is the wrong trade.
        if len(prefixed) > _DESCRIPTION_LIMIT:
            skipped_too_long += 1
            continue
        restored += 1
        conn.execute(
            _PROJECTS.update()
            .where(sa.cast(_PROJECTS.c.id, sa.Text) == str(row_id))
            .values(scenario_description=prefixed)
        )

    _LOG.info(
        "0004 downgrade: re-prefixed %d of %d project description(s)"
        "%s",
        restored, len(rows),
        f"; dropped the category on {skipped_too_long} row(s) whose description "
        f"would have exceeded {_DESCRIPTION_LIMIT} characters"
        if skipped_too_long else "",
    )
    op.drop_column("projects", "scenario_type")
