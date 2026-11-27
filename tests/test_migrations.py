"""Tests for versioned SQLite schema migrations (v2 -> v3)."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.database import CURRENT_SCHEMA_VERSION, DatabaseValidationError, connect, validate
from app.migrations import apply_migrations, get_schema_version, list_pending_migrations
from tools.migrate_db import main as migrate_db_main

SCHEMA_V2_PATH = Path(__file__).resolve().parents[1] / "app" / "schema_v2.sql"


def _create_v2_database_with_data(db_path: Path) -> dict[str, str]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.executescript(SCHEMA_V2_PATH.read_text(encoding="utf-8"))

    sys_id = str(uuid.uuid4())
    subj_id = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    topic_id = str(uuid.uuid4())
    student_id = str(uuid.uuid4())
    assign_id = str(uuid.uuid4())
    sub_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())

    with conn:
        conn.execute(
            "INSERT INTO subjects (id, name, name_key, is_system) VALUES (?, 'Без предмета', 'без предмета', 1);",
            (sys_id,),
        )
        conn.execute(
            "INSERT INTO subjects (id, name, name_key, is_system) VALUES (?, 'Алгоритмы', 'алгоритмы', 0);",
            (subj_id,),
        )
        conn.execute(
            "INSERT INTO groups (id, name, name_key) VALUES (?, 'ПИ-101', 'пи-101');",
            (group_id,),
        )
        conn.execute(
            "INSERT INTO group_access (group_id, password_hash, is_open, version) VALUES (?, 'hash', 1, 1);",
            (group_id,),
        )
        conn.execute(
            "INSERT INTO topics (id, subject_id, name, name_key) VALUES (?, ?, 'Сортировки', 'сортировки');",
            (topic_id, subj_id),
        )
        conn.execute(
            "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, 'Иванов Иван');",
            (student_id, group_id),
        )
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
            VALUES (?, ?, ?, '2026-11-10', 1, '2026-11-10T08:00:00Z', '2026-11-10T08:00:00Z');
            """,
            (assign_id, group_id, topic_id),
        )
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, reviewed_at, created_at)
            VALUES (?, ?, ?, ?, ?, 1, 'pending', NULL, '2026-11-10T09:00:00Z');
            """,
            (sub_id, student_id, group_id, topic_id, assign_id),
        )
        conn.execute(
            """
            INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
            VALUES (?, ?, 'sort.py', 'g/t/sort.py', 128, ?);
            """,
            (file_id, sub_id, "a" * 64),
        )
    conn.close()
    return {
        "group_id": group_id,
        "topic_id": topic_id,
        "student_id": student_id,
        "assign_id": assign_id,
        "sub_id": sub_id,
    }


def test_v2_database_fails_validate_with_migration_hint(tmp_path: Path) -> None:
    db_path = tmp_path / "v2.sqlite3"
    _create_v2_database_with_data(db_path)

    assert get_schema_version(db_path) == 2
    assert len(list_pending_migrations(2)) == 1

    with pytest.raises(DatabaseValidationError, match="migrate_db"):
        validate(db_path)


def test_migrate_v2_to_v3_preserves_data_and_creates_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    backup_dir = tmp_path / "backups"
    ids = _create_v2_database_with_data(db_path)

    res = apply_migrations(db_path, backup_dir=backup_dir, check_running=False)
    assert res.from_version == 2
    assert res.to_version == CURRENT_SCHEMA_VERSION
    assert res.applied_steps == ("add_grading_and_api_tokens",)
    assert res.backup_path is not None
    assert res.backup_path.is_file()
    assert get_schema_version(res.backup_path) == 2

    # Upgraded DB passes strict validation
    validate(db_path)
    assert get_schema_version(db_path) == CURRENT_SCHEMA_VERSION

    with connect(db_path) as conn:
        sub_row = conn.execute(
            "SELECT id, review_status, grade, feedback, graded_at, graded_by FROM submissions WHERE id = ?;",
            (ids["sub_id"],),
        ).fetchone()
        assert sub_row is not None
        assert sub_row["review_status"] == "pending"
        assert sub_row["grade"] is None
        assert sub_row["feedback"] is None

        mig_rows = conn.execute("SELECT version, name FROM schema_migrations;").fetchall()
        assert [(r["version"], r["name"]) for r in mig_rows] == [(3, "add_grading_and_api_tokens")]

        # Idempotent second run produces no-op
    res2 = apply_migrations(db_path, backup_dir=backup_dir, check_running=False)
    assert res2.applied_steps == ()
    assert res2.backup_path is None
    assert res2.to_version == CURRENT_SCHEMA_VERSION


def test_migrate_db_cli_status_and_upgrade(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "cli_v2.sqlite3"
    backup_dir = tmp_path / "cli_backups"
    _create_v2_database_with_data(db_path)

    rc_status = migrate_db_main(["status", "--database", str(db_path)])
    assert rc_status == 0
    status_data = json.loads(capsys.readouterr().out)
    assert status_data["current_version"] == 2
    assert status_data["target_version"] == CURRENT_SCHEMA_VERSION
    assert status_data["up_to_date"] is False
    assert len(status_data["pending_migrations"]) == 1

    rc_upgrade = migrate_db_main([
        "upgrade",
        "--offline",
        "--database", str(db_path),
        "--backup-dir", str(backup_dir),
        "--skip-running-check",
    ])
    assert rc_upgrade == 0
    out_upgrade = capsys.readouterr().out
    assert "Migrated" in out_upgrade

    rc_status2 = migrate_db_main(["status", "--database", str(db_path)])
    assert rc_status2 == 0
    status_data2 = json.loads(capsys.readouterr().out)
    assert status_data2["up_to_date"] is True
    assert status_data2["pending_migrations"] == []


def test_migrate_aborts_if_gunicorn_pid_is_running(tmp_path: Path) -> None:
    db_path = tmp_path / "running.sqlite3"
    _create_v2_database_with_data(db_path)

    pid_file = tmp_path / ".gunicorn.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Server process is running"):
        apply_migrations(db_path, check_running=True)

    assert get_schema_version(db_path) == 2
