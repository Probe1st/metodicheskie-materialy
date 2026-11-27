"""Versioned SQLite schema migrations (v2 -> v3+)."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .database import CURRENT_SCHEMA_VERSION, DatabaseValidationError, connect, validate


@dataclass(frozen=True, slots=True)
class MigrationStep:
    from_version: int
    to_version: int
    name: str
    fn: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class MigrationResult:
    from_version: int
    to_version: int
    applied_steps: tuple[str, ...]
    backup_path: Path | None


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """
    )


def migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Upgrade SQLite schema from v2 to v3 (grading columns, api_tokens, schema_migrations)."""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(submissions);").fetchall()
    }
    if "grade" not in cols:
        conn.execute(
            "ALTER TABLE submissions ADD COLUMN grade TEXT CHECK(grade IS NULL OR length(trim(grade))>0);"
        )
    if "feedback" not in cols:
        conn.execute("ALTER TABLE submissions ADD COLUMN feedback TEXT;")
    if "graded_at" not in cols:
        conn.execute("ALTER TABLE submissions ADD COLUMN graded_at TEXT;")
    if "graded_by" not in cols:
        conn.execute("ALTER TABLE submissions ADD COLUMN graded_by TEXT;")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(review_status, created_at DESC, id DESC);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submissions_student ON submissions(student_id, created_at DESC);"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_attachments (
            id TEXT PRIMARY KEY,
            original_name TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_contents (
            owner_kind TEXT NOT NULL CHECK (owner_kind IN ('topic', 'lesson')),
            owner_id TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            description_format TEXT NOT NULL CHECK (description_format IN ('html', 'md', 'txt')),
            attachment_id TEXT REFERENCES task_attachments(id) ON DELETE SET NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_kind, owner_id)
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_contents_attachment ON task_contents(attachment_id);"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_tokens (
            id TEXT NOT NULL PRIMARY KEY,
            name TEXT NOT NULL CHECK(length(trim(name))>0),
            name_key TEXT NOT NULL,
            token_prefix TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            scopes TEXT NOT NULL DEFAULT 'agent',
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT
        );
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_tokens_active_name ON api_tokens(name_key) WHERE revoked_at IS NULL;"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_tokens_prefix ON api_tokens(token_prefix);"
    )

    _ensure_schema_migrations_table(conn)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
        VALUES (?, ?, ?);
        """,
        (3, "add_grading_and_api_tokens", now_iso),
    )
    conn.execute("PRAGMA user_version = 3;")


MIGRATIONS: tuple[MigrationStep, ...] = (
    MigrationStep(
        from_version=2,
        to_version=3,
        name="add_grading_and_api_tokens",
        fn=migrate_v2_to_v3,
    ),
)


def get_schema_version(db_path: Path | str) -> int:
    """Read PRAGMA user_version from an existing SQLite database."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database file not found: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute("PRAGMA user_version;").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def list_pending_migrations(current_version: int) -> list[MigrationStep]:
    """Return ordered migration steps needed to reach CURRENT_SCHEMA_VERSION."""
    pending: list[MigrationStep] = []
    ver = current_version
    while ver < CURRENT_SCHEMA_VERSION:
        step = next((m for m in MIGRATIONS if m.from_version == ver), None)
        if step is None:
            raise DatabaseValidationError(
                f"No migration path from schema version {ver} to {CURRENT_SCHEMA_VERSION}"
            )
        pending.append(step)
        ver = step.to_version
    return pending


def check_no_running_server(candidate_dirs: Sequence[Path]) -> None:
    """Abort migration if a live .gunicorn.pid server process is detected."""
    for directory in candidate_dirs:
        pid_file = Path(directory) / ".gunicorn.pid"
        if not pid_file.is_file():
            continue
        try:
            pid_text = pid_file.read_text(encoding="utf-8").strip()
            if not pid_text.isdigit():
                continue
            pid = int(pid_text)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except PermissionError as err:
                raise RuntimeError(
                    f"Server process is running (PID {pid} from {pid_file}). Stop server before migration."
                ) from err
            raise RuntimeError(
                f"Server process is running (PID {pid} from {pid_file}). Stop server before migration."
            )
        except OSError:
            continue


def create_db_backup(db_path: Path | str, backup_dir: Path | str) -> Path:
    """Create a verified online SQLite backup before running migrations."""
    src_path = Path(db_path).resolve()
    if not src_path.is_file():
        raise FileNotFoundError(f"Database file not found: {src_path}")

    bdir = Path(backup_dir).resolve()
    bdir.mkdir(parents=True, exist_ok=True)
    try:
        bdir.chmod(0o700)
    except OSError:
        pass

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = bdir / f"{src_path.stem}.{stamp}.{uuid.uuid4().hex[:8]}.sqlite3"

    src_conn = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    try:
        backup_path.chmod(0o600)
    except OSError:
        pass

    with sqlite3.connect(str(backup_path)) as check_conn:
        res = check_conn.execute("PRAGMA integrity_check;").fetchall()
        if not res or res[0][0] != "ok":
            backup_path.unlink(missing_ok=True)
            raise RuntimeError(f"Backup integrity check failed: {res}")

    return backup_path


def apply_migrations(
    db_path: Path | str,
    *,
    backup_dir: Path | str | None = None,
    check_running: bool = True,
) -> MigrationResult:
    """Apply all pending migrations safely with backup and post-migration validation."""
    target = Path(db_path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Database file not found: {target}")

    if check_running:
        check_no_running_server([Path("."), target.parent, target.parent.parent])

    current_version = get_schema_version(target)
    if current_version > CURRENT_SCHEMA_VERSION:
        raise DatabaseValidationError(
            f"Database user_version {current_version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )
    if current_version < 2:
        raise DatabaseValidationError(
            f"Unsupported database user_version {current_version}. Use tools.migrate_v2 for legacy databases."
        )

    pending = list_pending_migrations(current_version)
    if not pending:
        validate(target)
        return MigrationResult(
            from_version=current_version,
            to_version=current_version,
            applied_steps=(),
            backup_path=None,
        )

    resolved_backup_dir = (
        Path(backup_dir).resolve()
        if backup_dir is not None
        else target.parent / "backups"
    )
    backup_path = create_db_backup(target, resolved_backup_dir)

    applied_names: list[str] = []
    with connect(target) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        for step in pending:
            step.fn(conn)
            applied_names.append(step.name)

        fk_errors = conn.execute("PRAGMA foreign_key_check;").fetchall()
        if fk_errors:
            raise DatabaseValidationError(f"Foreign key violations after migration: {fk_errors}")

        ic_rows = conn.execute("PRAGMA integrity_check;").fetchall()
        if not ic_rows or ic_rows[0][0] != "ok":
            raise DatabaseValidationError(f"Integrity check failed after migration: {ic_rows}")

    validate(target)
    return MigrationResult(
        from_version=current_version,
        to_version=CURRENT_SCHEMA_VERSION,
        applied_steps=tuple(applied_names),
        backup_path=backup_path,
    )
