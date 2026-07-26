from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import User
from services.auth_service import hash_password
from services.tenancy_service import normalize_email
from settings import get_settings


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def bootstrap_super_admin(*, email: str, password: str) -> User:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    try:
        with session_local() as db:
            normalized_email = normalize_email(email)
            user = db.scalar(select(User).where(User.email == normalized_email))
            if user is None:
                user = User(
                    email=normalized_email,
                    password_hash=hash_password(password),
                    status="active",
                    is_super_admin=True,
                    created_at=_now_utc(),
                )
                db.add(user)
            else:
                user.password_hash = hash_password(password)
                user.status = "active"
                user.is_super_admin = True

            db.commit()
            db.refresh(user)
            return user
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or update the platform super-admin")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    user = bootstrap_super_admin(email=args.email, password=args.password)
    print(f"Bootstrapped super-admin {user.email} ({user.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
