"""
Report drift between the projects directory and the database.

    pixi run python -m tools.reconcile_storage
    pixi run python -m tools.reconcile_storage --sweep-tmp

Read-only unless `--sweep-tmp` is passed, and even then it removes only the
`*.tmp` files a killed save left behind. Orphan directories and rows whose
directory is gone are REPORTED, never acted on — see `storage_reconcile`'s
module docstring for why adopting an orphan automatically is the wrong default.

`DATABASE_URL` is mandatory for any manual run: `.env` carries a CWD-relative
`sqlite+pysqlite:///./auth_dev.db`, dotenv outranks field defaults, and
`python -m tools....` runs with cwd `pypsa-gui/backend` — exactly where that
resolves. Set it explicitly or this reports on the developer database.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import project_registry, storage_reconcile  # noqa: E402
from services.storage_paths import use_org_segment  # noqa: E402
from settings import get_settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sweep-tmp",
        action="store_true",
        help="delete the leftover *.tmp files this run reports (nothing else)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    root = Path(settings.projects_root)
    org_segment = use_org_segment()

    from db.session import SessionLocal

    with SessionLocal() as db:
        report = storage_reconcile.scan(db, root, org_segment=org_segment)

    print(f"database: {settings.database_url}")
    print(f"projects: {root}  (org segment: {'yes' if org_segment else 'no'})")

    if not report.has_findings:
        print("clean — every row has a directory and every directory has a row")
        return 0

    for directory in report.orphan_dirs:
        print(f"orphan directory (no row): {directory}")
    for row in report.missing_dirs:
        # The RESOLVED path, not the raw column — that is the directory the app
        # actually looked in, and for a relative row the raw value on its own
        # is not somewhere the user can go and look.
        print(
            f"missing directory for '{row.name}' ({row.id}): "
            f"{project_registry.project_dir(row)}"
        )
    for path in report.stale_tmp:
        print(f"leftover .tmp (a save was killed mid-write): {path}")

    if args.sweep_tmp:
        for path in storage_reconcile.sweep_tmp(report):
            print(f"removed: {path}")
    elif report.stale_tmp:
        print("re-run with --sweep-tmp to remove the .tmp files above")

    if report.orphan_dirs or report.missing_dirs:
        print(
            "\nNothing was changed. An orphan directory may be a project worth "
            "keeping and a missing directory may be a backup worth restoring; "
            "both are decisions this tool deliberately leaves to you."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
