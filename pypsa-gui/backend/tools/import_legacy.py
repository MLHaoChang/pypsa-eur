"""
Import projects from a pre-desktop install.

    pixi run python -m tools.import_legacy                 # dry run (default)
    pixi run python -m tools.import_legacy --apply
    pixi run python -m tools.import_legacy --rollback <manifest>
    pixi run python -m tools.import_legacy --forget-legacy
    pixi run python -m tools.import_legacy --rebase-db      # separate, see below

The source root comes from `PYPSAGUI_LEGACY_IMPORT_ROOT`, or `--source`.

**`DATABASE_URL` is mandatory for any manual run.** `.env` carries a
CWD-relative `sqlite+pysqlite:///./auth_dev.db`, dotenv outranks field
defaults, and `python -m tools....` runs with cwd `pypsa-gui/backend` — exactly
where that resolves. `--apply` REFUSES when the database resolves inside the
source checkout, because writing there means importing into the developer
database and out to `~/Documents` in one go.

`--rebase-db` is separate and separately confirmed. It is the only thing that
delivers E2 for a row whose `storage_path` points outside `projects_root` —
which migration 0003 leaves alone by design — and it necessarily targets a
database inside the checkout, which is why it cannot live under `--apply`'s
refusal. It prompts, names the database and the row, and defaults to no.

`--forget-legacy` moves the old tree OUT of the checkout, by default to
`~/PyPSA GUI legacy-<date>/`. Renaming it in place would leave it untracked
*and* un-ignored — `.gitignore` covers `backend/projects/` specifically — so
`git status` would go permanently dirty and a plain `git clean -df` would
reach it. Deleting is always your decision; this only moves.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app_paths  # noqa: E402
from services import legacy_import, project_registry  # noqa: E402
from settings import get_settings  # noqa: E402

_CHECKOUT = Path(__file__).resolve().parent.parent


def _database_is_inside_the_checkout(url: str) -> bool:
    if "sqlite" not in url or ":memory:" in url:
        return False
    raw = url.split("///", 1)[-1]
    try:
        resolved = Path(raw).resolve()
    except OSError:
        return False
    return resolved.is_relative_to(_CHECKOUT)


def _source_root(args) -> Path | None:
    if args.source:
        return Path(args.source).expanduser().resolve()
    configured = get_settings().legacy_import_root
    return Path(configured).expanduser().resolve() if configured else None


def _print_report(report) -> None:
    for name in report.would_import:
        print(f"would import: {name}")
    for name in report.imported:
        print(f"imported:     {name}")
    for name in report.already_imported:
        print(f"already here: {name}")
    for line in report.collisions:
        print(f"collision:    {line}")
    for line in report.failed:
        print(f"FAILED:       {line}")
    for line in report.warnings:
        print(f"warning:      {line}")


def _confirm(question: str) -> bool:
    """Defaults to no. Every destructive path here goes through this."""
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _do_import(args) -> int:
    settings = get_settings()
    source = _source_root(args)
    if source is None:
        print(
            "No legacy root configured. Set PYPSAGUI_LEGACY_IMPORT_ROOT or pass "
            "--source. (Until the packaged shell sets it, the first-run import "
            "does not fire for a plain checkout — a declared F1 deviation.)"
        )
        return 2
    if not source.is_dir():
        print(f"Legacy root does not exist: {source}")
        return 2

    if args.apply and _database_is_inside_the_checkout(settings.database_url):
        print(
            f"Refusing --apply: DATABASE_URL resolves inside the checkout\n"
            f"  {settings.database_url}\n"
            "That is the developer database, and importing into it would also "
            "copy 100+ MB into your real Documents folder. Set DATABASE_URL "
            "explicitly, or use --rebase-db if you meant to rebase that "
            "database's rows."
        )
        return 2

    print(f"source:   {source}")
    print(f"database: {settings.database_url}")
    print(f"projects: {settings.projects_root}")

    from db.session import SessionLocal
    import local_mode

    with SessionLocal() as db:
        user = local_mode.get_local_user(db)
        if user is None:
            print("No local identity. Run `python -m tools.bootstrap_local` first.")
            return 2
        org_id = local_mode.LOCAL_ORG_ID
        report = legacy_import.import_all(
            db, source, org_id, user.id, apply=args.apply
        )
        _print_report(report)

        if args.apply and report.records:
            stamp = datetime.now(tz=timezone.utc)
            manifest_path = (
                app_paths.app_data_dir()
                / f"import-manifest-{stamp:%Y%m%dT%H%M%S}.json"
            )
            try:
                legacy_import.write_manifest(
                    report,
                    source_root=source,
                    dest_root=Path(settings.projects_root),
                    path=manifest_path,
                )
            except OSError as exc:
                # An import with no manifest is an import with no way back.
                print(f"FAILED to write the run manifest: {exc}")
                return 1
            print(f"manifest: {manifest_path}")
            print("Roll this run back with --rollback <manifest>.")

    if not args.apply:
        print("\nDry run — nothing was changed. Re-run with --apply.")
    else:
        print(
            "\nThe original tree was COPIED, not moved, and is still where it "
            "was. Check the import, then run --forget-legacy to move it aside."
        )
    return 0


def _do_rollback(args) -> int:
    manifest = Path(args.rollback).expanduser().resolve()
    if not manifest.is_file():
        print(f"No such manifest: {manifest}")
        return 2
    if not _confirm(f"Delete every project and row recorded in {manifest.name}?"):
        print("Nothing was changed.")
        return 0

    from db.session import SessionLocal

    with SessionLocal() as db:
        for name in legacy_import.rollback(db, manifest):
            print(f"rolled back: {name}")
    return 0


def _do_forget_legacy(args) -> int:
    source = _source_root(args)
    if source is None or not source.is_dir():
        print("Nothing to retire — no legacy root configured or it is gone.")
        return 2

    destination = (
        Path(args.destination).expanduser().resolve()
        if args.destination
        else Path.home() / f"PyPSA GUI legacy-{date.today():%Y%m%d}"
    )
    if destination.exists():
        print(f"Refusing: {destination} already exists.")
        return 2
    if destination.is_relative_to(_CHECKOUT):
        print(
            f"Refusing: {destination} is inside the checkout. A retired tree "
            "there is untracked AND un-ignored, so `git status` goes "
            "permanently dirty and a plain `git clean -df` deletes it."
        )
        return 2

    print(f"Move {source}\n  ->  {destination}")
    if not _confirm("Move it?"):
        print("Nothing was changed.")
        return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    print(f"Moved. Your old projects are at {destination}.")
    print("Nothing was deleted — remove it yourself once you are satisfied.")
    return 0


def _do_rebase_db(args) -> int:
    settings = get_settings()
    from sqlalchemy import select

    from db.models import Project
    from db.session import SessionLocal

    with SessionLocal() as db:
        rows = [
            row
            for row in db.scalars(select(Project)).all()
            if project_registry.stores_absolute_path(row)
        ]
        if not rows:
            print("Nothing to rebase — every row is already relative to the root.")
            return 0

        print(f"database: {settings.database_url}")
        print(f"projects: {settings.projects_root}")
        for row in rows:
            print(f"  {row.name}  ->  {project_registry.project_dir(row)}")
        print(
            "\nEach project is COPIED into the readable layout and verified "
            "before its row is repointed. The originals are left alone."
        )
        if not _confirm(f"Rebase {len(rows)} project(s)?"):
            print("Nothing was changed.")
            return 0

        for row in rows:
            try:
                destination = legacy_import.rebase_row(db, row)
            except Exception as exc:  # noqa: BLE001 — report and keep going
                print(f"FAILED {row.name}: {exc}")
                continue
            print(f"rebased {row.name} -> {destination}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="legacy root (default: PYPSAGUI_LEGACY_IMPORT_ROOT)")
    parser.add_argument("--apply", action="store_true", help="actually import")
    parser.add_argument("--rollback", metavar="MANIFEST", help="undo one recorded run")
    parser.add_argument(
        "--forget-legacy", action="store_true",
        help="move the old tree out of the checkout (never deletes)",
    )
    parser.add_argument("--destination", help="where --forget-legacy moves the tree")
    parser.add_argument(
        "--rebase-db", action="store_true",
        help="copy rows whose storage_path is absolute into the readable layout",
    )
    args = parser.parse_args(argv)

    if args.rollback:
        return _do_rollback(args)
    if args.forget_legacy:
        return _do_forget_legacy(args)
    if args.rebase_db:
        return _do_rebase_db(args)
    return _do_import(args)


if __name__ == "__main__":
    raise SystemExit(main())
