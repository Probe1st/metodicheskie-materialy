"""Pytest fixtures for homework-submissions SQLite v2 audit suite.

Provides isolated workspace directories, database initialization helpers,
and store instances for audit test execution.
"""

from __future__ import annotations

import shutil
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.access import AccessStore
from app.assignments import AssignmentStore
from app.database import connect, initialize, normalize_name_key
from app.sqlite_catalog import SqliteCatalogStore
from app.submissions import SubmissionStore
from tests.audit.support import (
    A1_DATE,
    A1_ID,
    A1_PAIR,
    A2_DATE,
    A2_ID,
    A2_PAIR,
    FUTURE_DEADLINE,
    G1_ACCESS_VERSION,
    G1_ID,
    G1_NAME,
    G1_PASSWORD_HASH,
    G2_ACCESS_VERSION,
    G2_ID,
    G2_NAME,
    G2_PASSWORD_HASH,
    S1_ID,
    S1_NAME,
    S2_ID,
    S2_NAME,
    STUDENT1_ID,
    STUDENT1_NAME,
    STUDENT2_ID,
    STUDENT2_NAME,
    SYSTEM_UNASSIGNED_SUBJECT_ID,
    T1_ID,
    T1_NAME,
    T2_ID,
    T2_NAME,
    T_FREE_ID,
    T_FREE_NAME,
)


@dataclass(frozen=True, slots=True)
class AuditEnv:
    """Isolated environment paths and helper methods for audit tests."""

    root: Path
    source: Path
    target: Path
    backup: Path
    sentinel: Path
    storage: Path
    private: Path

    @property
    def db_path(self) -> Path:
        """Default database path inside isolated private directory."""
        return self.private / "data.sqlite3"

    def init_db(self, path: Path | None = None) -> Path:
        """Initialize a fresh schema v2 SQLite database."""
        target_path = path or self.db_path
        initialize(target_path)
        return target_path

    def get_catalog_store(self, db_path: Path | None = None) -> SqliteCatalogStore:
        """Get a SqliteCatalogStore bound to the isolated database."""
        return SqliteCatalogStore(db_path or self.db_path)

    def get_access_store(self, db_path: Path | None = None) -> AccessStore:
        """Get an AccessStore bound to the isolated database."""
        return AccessStore(db_path or self.db_path)

    def get_assignment_store(self, db_path: Path | None = None) -> AssignmentStore:
        """Get an AssignmentStore bound to the isolated database."""
        return AssignmentStore(db_path or self.db_path)

    def get_submission_store(
        self, db_path: Path | None = None, storage_root: Path | None = None
    ) -> SubmissionStore:
        """Get a SubmissionStore bound to the isolated database and storage."""
        return SubmissionStore(db_path or self.db_path, storage_root or self.storage)

    def populate_canonical(self, db_path: Path | None = None) -> Path:
        """Seed the database with canonical test fixtures from support.py."""
        target_path = db_path or self.db_path
        if not target_path.is_file():
            self.init_db(target_path)

        with connect(target_path) as conn:
            # Groups G1 and G2
            conn.execute(
                "INSERT OR IGNORE INTO groups (id, name, name_key, archived_at) VALUES (?, ?, ?, NULL);",
                (str(G1_ID), G1_NAME, normalize_name_key(G1_NAME)),
            )
            conn.execute(
                "INSERT OR IGNORE INTO groups (id, name, name_key, archived_at) VALUES (?, ?, ?, NULL);",
                (str(G2_ID), G2_NAME, normalize_name_key(G2_NAME)),
            )

            # Group access with version=7 and hashed passwords
            conn.execute(
                """
                INSERT OR REPLACE INTO group_access (group_id, password_hash, is_open, closes_at_utc, version)
                VALUES (?, ?, ?, ?, ?);
                """,
                (str(G1_ID), G1_PASSWORD_HASH, 1, FUTURE_DEADLINE, G1_ACCESS_VERSION),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO group_access (group_id, password_hash, is_open, closes_at_utc, version)
                VALUES (?, ?, ?, ?, ?);
                """,
                (str(G2_ID), G2_PASSWORD_HASH, 1, FUTURE_DEADLINE, G2_ACCESS_VERSION),
            )

            # Subjects S1 and S2
            conn.execute(
                "INSERT OR IGNORE INTO subjects (id, name, name_key, is_system, archived_at) VALUES (?, ?, ?, 0, NULL);",
                (str(S1_ID), S1_NAME, normalize_name_key(S1_NAME)),
            )
            conn.execute(
                "INSERT OR IGNORE INTO subjects (id, name, name_key, is_system, archived_at) VALUES (?, ?, ?, 0, NULL);",
                (str(S2_ID), S2_NAME, normalize_name_key(S2_NAME)),
            )

            # Topics T1 (in S1) and T2 (in S2) with identical name
            conn.execute(
                "INSERT OR IGNORE INTO topics (id, subject_id, name, name_key, archived_at) VALUES (?, ?, ?, ?, NULL);",
                (str(T1_ID), str(S1_ID), T1_NAME, normalize_name_key(T1_NAME)),
            )
            conn.execute(
                "INSERT OR IGNORE INTO topics (id, subject_id, name, name_key, archived_at) VALUES (?, ?, ?, ?, NULL);",
                (str(T2_ID), str(S2_ID), T2_NAME, normalize_name_key(T2_NAME)),
            )

            # System unassigned subject ID
            row = conn.execute("SELECT id FROM subjects WHERE is_system=1;").fetchone()
            system_id = row[0] if row else str(SYSTEM_UNASSIGNED_SUBJECT_ID)

            # Free topic in system subject
            conn.execute(
                "INSERT OR IGNORE INTO topics (id, subject_id, name, name_key, archived_at) VALUES (?, ?, ?, ?, NULL);",
                (str(T_FREE_ID), system_id, T_FREE_NAME, normalize_name_key(T_FREE_NAME)),
            )

            # Two assignments for same topic on different dates and pairs
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT OR IGNORE INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL);
                """,
                (str(A1_ID), str(G1_ID), str(T1_ID), A1_DATE, A1_PAIR, now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL);
                """,
                (str(A2_ID), str(G1_ID), str(T1_ID), A2_DATE, A2_PAIR, now_iso, now_iso),
            )

            # Two students with identical name "Иванов Иван" but different UUIDs in G1
            conn.execute(
                "INSERT OR IGNORE INTO students (id, group_id, display_name, archived_at) VALUES (?, ?, ?, NULL);",
                (str(STUDENT1_ID), str(G1_ID), STUDENT1_NAME),
            )
            conn.execute(
                "INSERT OR IGNORE INTO students (id, group_id, display_name, archived_at) VALUES (?, ?, ?, NULL);",
                (str(STUDENT2_ID), str(G1_ID), STUDENT2_NAME),
            )
            conn.commit()

        return target_path


@pytest.fixture
def audit_env(tmp_path: Path) -> Generator[AuditEnv, None, None]:
    """Sets up an isolated directory structure for audit test cases.

    Directories created:
      - source
      - target
      - backup
      - sentinel
      - storage
      - private
    """
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    backup_dir = tmp_path / "backup"
    sentinel_dir = tmp_path / "sentinel"
    storage_dir = tmp_path / "storage"
    private_dir = tmp_path / "private"

    for d in (source_dir, target_dir, backup_dir, sentinel_dir, storage_dir, private_dir):
        d.mkdir(parents=True, exist_ok=True)

    env = AuditEnv(
        root=tmp_path,
        source=source_dir,
        target=target_dir,
        backup=backup_dir,
        sentinel=sentinel_dir,
        storage=storage_dir,
        private=private_dir,
    )

    yield env

    # Teardown: ensure clean workspace removal
    for d in (source_dir, target_dir, backup_dir, sentinel_dir, storage_dir, private_dir):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
