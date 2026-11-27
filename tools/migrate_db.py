"""CLI tool for applying versioned SQLite schema migrations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from app.database import CURRENT_SCHEMA_VERSION, DatabaseError
from app.migrations import (
    apply_migrations,
    get_schema_version,
    list_pending_migrations,
)


def _default_database_path() -> Path:
    raw = os.getenv("DATABASE_PATH")
    if raw:
        return Path(raw)
    private_dir = Path(os.getenv("PRIVATE_DATA_DIR", ".private"))
    return private_dir / "app_v2.sqlite3"


def cmd_status(args: argparse.Namespace) -> int:
    db_path = Path(args.database).resolve()
    if not db_path.is_file():
        sys.stderr.write(f"Error: Database not found: {db_path}\n")
        return 1

    current = get_schema_version(db_path)
    pending = list_pending_migrations(current) if current >= 2 and current <= CURRENT_SCHEMA_VERSION else []
    payload = {
        "database": str(db_path),
        "current_version": current,
        "target_version": CURRENT_SCHEMA_VERSION,
        "up_to_date": current == CURRENT_SCHEMA_VERSION,
        "pending_migrations": [
            {"from": m.from_version, "to": m.to_version, "name": m.name}
            for m in pending
        ],
    }
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    if not args.offline:
        sys.stderr.write("Error: Database upgrade requires --offline flag.\n")
        return 1

    db_path = Path(args.database).resolve()
    try:
        result = apply_migrations(
            db_path,
            backup_dir=args.backup_dir,
            check_running=not args.skip_running_check,
        )
    except (FileNotFoundError, DatabaseError, RuntimeError, OSError) as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1

    if not result.applied_steps:
        sys.stdout.write(
            f"Database {db_path} is already up to date (user_version={result.to_version}).\n"
        )
    else:
        sys.stdout.write(
            f"Migrated {db_path} from v{result.from_version} to v{result.to_version} "
            f"(steps: {', '.join(result.applied_steps)}; backup: {result.backup_path}).\n"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.migrate_db",
        description="Versioned SQLite schema migration tool.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_status = subparsers.add_parser("status", help="Show current and target schema versions.")
    p_status.add_argument(
        "--database",
        "-d",
        type=Path,
        default=_default_database_path(),
        help="Path to SQLite database",
    )
    p_status.set_defaults(func=cmd_status)

    p_upgrade = subparsers.add_parser("upgrade", help="Backup and migrate database to latest schema version.")
    p_upgrade.add_argument("--offline", action="store_true", required=True, help="Explicitly confirm offline migration")
    p_upgrade.add_argument(
        "--database",
        "-d",
        type=Path,
        default=_default_database_path(),
        help="Path to SQLite database",
    )
    p_upgrade.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Directory to store pre-migration backup (defaults to <db_dir>/backups)",
    )
    p_upgrade.add_argument(
        "--skip-running-check",
        action="store_true",
        default=False,
        help="Skip .gunicorn.pid check (for isolated tests)",
    )
    p_upgrade.set_defaults(func=cmd_upgrade)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
