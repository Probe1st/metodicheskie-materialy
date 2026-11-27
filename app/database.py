"""Database connectivity, initialization, and validation for SQLite v3 schema."""

from __future__ import annotations

import errno
import os
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

CURRENT_SCHEMA_VERSION = 3
SCHEMA_PATH = Path(__file__).resolve().parent / "schema_v3.sql"

REQUIRED_TABLES: frozenset[str] = frozenset({
    "groups",
    "subjects",
    "topics",
    "group_access",
    "students",
    "class_assignments",
    "submissions",
    "submission_files",
    "task_attachments",
    "task_contents",
    "api_tokens",
    "schema_migrations",
})
__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "REQUIRED_TABLES",
    "DatabaseError",
    "DatabaseNotFoundError",
    "DatabaseValidationError",
    "connect",
    "create_schema",
    "initialize",
    "normalize_name_key",
    "validate",
    "validate_lesson_date",
    "validate_pair_number",
]


_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DatabaseError(Exception):
    """Base exception for database operations."""


class DatabaseValidationError(DatabaseError):
    """Raised when database schema, version, or integrity validation fails."""


class DatabaseNotFoundError(DatabaseValidationError, FileNotFoundError):
    """Raised when the database file is not found during validation."""


@contextmanager
def connect(path: Path | str) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connections with foreign keys and WAL timeout configured."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def normalize_name_key(name: str) -> str:
    """Normalize names for unique constraint checks using NFC normalization and casefolding."""
    if not isinstance(name, str):
        raise TypeError(f"Name must be a string, got {type(name).__name__}")
    stripped = name.strip()
    if not stripped:
        raise ValueError("Name cannot be empty")
    return unicodedata.normalize("NFC", stripped).casefold()


def create_schema(conn: sqlite3.Connection) -> None:
    """Load and execute schema_v3.sql on the given connection."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)


def initialize(path: Path | str) -> None:
    """Create a new database with schema v3 and the initial system subject.

    Raises FileExistsError if the database already exists.
    """
    path = Path(path).resolve()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        parent.chmod(0o700)
    except OSError:
        pass

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except (FileExistsError, OSError) as err:
        if isinstance(err, FileExistsError) or err.errno == errno.EEXIST:
            raise FileExistsError(f"Database already exists at {path}") from None
        raise
    os.close(fd)

    try:
        path.chmod(0o600)
    except OSError:
        pass

    try:
        with connect(path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            create_schema(conn)

            system_id = str(uuid.uuid4())
            system_name = "Без предмета"
            system_name_key = normalize_name_key(system_name)
            conn.execute(
                """
                INSERT INTO subjects (id, name, name_key, is_system, archived_at)
                VALUES (?, ?, ?, 1, NULL);
                """,
                (system_id, system_name, system_name_key),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
                VALUES (?, ?, ?);
                """,
                (CURRENT_SCHEMA_VERSION, "initial_v3", datetime.now(timezone.utc).isoformat()),
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise

    try:
        path.chmod(0o600)
    except OSError:
        pass


def validate(path: Path | str) -> None:
    """Validate that the database at path exists, has current user_version, passes integrity checks, and contains all tables.

    Raises DatabaseNotFoundError / FileNotFoundError if the file does not exist,
    or DatabaseValidationError if any check fails.
    """
    path = Path(path)
    if not path.is_file():
        raise DatabaseNotFoundError(f"Database file not found: {path}")

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise DatabaseValidationError(f"Could not open database file: {e}") from e

    try:
        try:
            ver_row = conn.execute("PRAGMA user_version;").fetchone()
            if not ver_row or ver_row[0] != CURRENT_SCHEMA_VERSION:
                ver_val = ver_row[0] if ver_row else None
                hint = ""
                if ver_val == 2:
                    hint = " Run 'python -m tools.migrate_db upgrade --offline' or './manage.sh migrate'."
                raise DatabaseValidationError(
                    f"Unsupported database user_version: {ver_val}, expected {CURRENT_SCHEMA_VERSION}.{hint}"
                )

            qc_rows = conn.execute("PRAGMA quick_check;").fetchall()
            if not qc_rows or qc_rows[0][0] != "ok":
                raise DatabaseValidationError(f"Database integrity check failed: {qc_rows}")

            tables_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
            existing_tables = {row[0] for row in tables_rows}
            missing_tables = REQUIRED_TABLES - existing_tables
            if missing_tables:
                raise DatabaseValidationError(
                    f"Missing required tables: {sorted(missing_tables)}"
                )
        except sqlite3.Error as e:
            raise DatabaseValidationError(f"Database error during validation: {e}") from e
    finally:
        conn.close()


def validate_lesson_date(lesson_date: str) -> str:
    """Validate calendar date format (YYYY-MM-DD) and validity."""
    if not isinstance(lesson_date, str):
        raise TypeError(f"lesson_date must be a string, got {type(lesson_date).__name__}")
    if not _DATE_REGEX.match(lesson_date):
        raise ValueError(
            f"Invalid lesson date format: '{lesson_date}', expected YYYY-MM-DD"
        )
    try:
        datetime.strptime(lesson_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"Invalid calendar date: '{lesson_date}'") from e
    return lesson_date


def validate_pair_number(pair_number: int) -> int:
    """Validate that pair_number is an integer between 1 and 6."""
    if isinstance(pair_number, bool) or not isinstance(pair_number, int):
        raise TypeError(
            f"pair_number must be an integer, got {type(pair_number).__name__}"
        )
    if not (1 <= pair_number <= 6):
        raise ValueError(f"pair_number must be between 1 and 6, got {pair_number}")
    return pair_number
