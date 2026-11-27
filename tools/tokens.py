"""CLI tool for managing AI agent API tokens."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from app.database import DatabaseError, validate
from app.tokens import TokenError, TokenStore


def _default_database_path() -> Path:
    raw = os.getenv("DATABASE_PATH")
    if raw:
        return Path(raw)
    private_dir = Path(os.getenv("PRIVATE_DATA_DIR", ".private"))
    return private_dir / "app_v2.sqlite3"


def cmd_create(args: argparse.Namespace) -> int:
    db_path = Path(args.database).resolve()
    try:
        validate(db_path)
        store = TokenStore(db_path)
        token_obj, raw_token = store.create_token(args.name, scopes=args.scopes)
    except (DatabaseError, TokenError, OSError) as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1

    payload = {
        "id": str(token_obj.id),
        "name": token_obj.name,
        "token_prefix": token_obj.token_prefix,
        "scopes": token_obj.scopes,
        "created_at": token_obj.created_at.isoformat(),
        "token": raw_token,
    }
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    db_path = Path(args.database).resolve()
    try:
        validate(db_path)
        store = TokenStore(db_path)
        tokens = store.list_tokens(include_revoked=not args.active_only)
    except (DatabaseError, OSError) as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1

    payload = [
        {
            "id": str(t.id),
            "name": t.name,
            "token_prefix": t.token_prefix,
            "scopes": t.scopes,
            "created_at": t.created_at.isoformat(),
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
        }
        for t in tokens
    ]
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    db_path = Path(args.database).resolve()
    try:
        uid = UUID(args.id)
        validate(db_path)
        store = TokenStore(db_path)
        token_obj = store.revoke_token(uid)
    except (ValueError, DatabaseError, TokenError, OSError) as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1

    payload = {
        "id": str(token_obj.id),
        "name": token_obj.name,
        "revoked_at": token_obj.revoked_at.isoformat() if token_obj.revoked_at else None,
    }
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.tokens",
        description="Manage AI agent API tokens.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_create = subparsers.add_parser("create", help="Create a new API token.")
    p_create.add_argument("--database", "-d", type=Path, default=_default_database_path())
    p_create.add_argument("--name", "-n", required=True, help="Human-readable agent/token name")
    p_create.add_argument("--scopes", default="agent", help="Token scopes (default: agent)")
    p_create.set_defaults(func=cmd_create)

    p_list = subparsers.add_parser("list", help="List API tokens.")
    p_list.add_argument("--database", "-d", type=Path, default=_default_database_path())
    p_list.add_argument("--active-only", action="store_true", default=False)
    p_list.set_defaults(func=cmd_list)

    p_revoke = subparsers.add_parser("revoke", help="Revoke an API token by UUID.")
    p_revoke.add_argument("--database", "-d", type=Path, default=_default_database_path())
    p_revoke.add_argument("--id", required=True, help="Token UUID")
    p_revoke.set_defaults(func=cmd_revoke)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
