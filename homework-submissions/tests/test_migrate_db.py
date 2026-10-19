from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from tools.migrate_db import (
    HISTORICAL_LESSONS,
    HistoricalLesson,
    connect_db,
    dry_run_db_migration,
    ensure_schema,
    execute_db_migration,
    main,
    resolve_assignments,
    rollback_db_migration,
)


@pytest.fixture
def catalog_file() -> Path:
    # Use real catalog if it exists
    real_catalog = Path("homework-submissions/catalog.json")
    if real_catalog.is_file():
        return real_catalog
    raise FileNotFoundError("catalog.json not found")


def test_schema_creation_and_constraints(tmp_path: Path):
    db_path = tmp_path / "test_access.sqlite3"
    with connect_db(db_path) as conn:
        ensure_schema(conn)

        # Check table exists
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='class_assignments';")
        assert cur.fetchone() is not None

        # Check indexes exist
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_assignments_group_subject';")
        assert cur.fetchone() is not None
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_assignments_group_date';")
        assert cur.fetchone() is not None

        # Check CHECK constraint on pair_number (must be 1..6)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO class_assignments (
                    id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
                ) VALUES ('id1', 'grp', 's1', 't1', '2026-10-19', 7, '2026-10-19', '2026-10-19');
                """
            )

        # Valid insert
        conn.execute(
            """
            INSERT INTO class_assignments (
                id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
            ) VALUES ('id1', 'grp', 's1', 't1', '2026-10-19', 1, '2026-10-19', '2026-10-19');
            """
        )

        # Check UNIQUE constraint on (group_name, lesson_date, pair_number)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO class_assignments (
                    id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
                ) VALUES ('id2', 'grp', 's2', 't2', '2026-10-19', 1, '2026-10-19', '2026-10-19');
                """
            )


def test_historical_lessons_resolution_26_items(catalog_file: Path):
    resolved = resolve_assignments(catalog_file, HISTORICAL_LESSONS)
    assert len(resolved) == 26

    # Verify each resolved lesson has non-empty fields
    for item in resolved:
        assert item["id"]
        assert item["group_name"]
        assert item["subject_id"]
        assert item["topic_id"]
        assert item["lesson_date"]
        assert 1 <= item["pair_number"] <= 6


def test_resolution_missing_subject_or_topic_raises_error(tmp_path: Path):
    bad_catalog = tmp_path / "bad_catalog.json"
    bad_catalog.write_text(
        json.dumps({
            "version": 3,
            "subjects": [{"id": "s1", "name": "Предмет 1"}],
            "topics": [{"id": "t1", "subject_id": "s1", "name": "Тема 1"}],
        }),
        encoding="utf-8",
    )

    # Missing subject
    missing_subj_lesson = [HistoricalLesson("Гр 1", "Неизвестный предмет", "Тема 1", "2026-10-01", 1)]
    with pytest.raises(ValueError, match="Subject not found in catalog"):
        resolve_assignments(bad_catalog, missing_subj_lesson)

    # Missing topic
    missing_topic_lesson = [HistoricalLesson("Гр 1", "Предмет 1", "Неизвестная тема", "2026-10-01", 1)]
    with pytest.raises(ValueError, match="Topic 'Неизвестная тема' not found"):
        resolve_assignments(bad_catalog, missing_topic_lesson)


def test_dry_run_db_migration(tmp_path: Path, catalog_file: Path):
    db_path = tmp_path / "dry_run.sqlite3"

    res = dry_run_db_migration(db_path, catalog_file)
    assert res["mode"] == "dry-run"
    assert res["total_lessons"] == 26
    assert res["to_insert"] == 26
    assert res["already_existing"] == 0
    assert not db_path.exists()  # DB must not be created on dry run


def test_execute_migration_and_idempotency(tmp_path: Path, catalog_file: Path):
    db_path = tmp_path / "test_exec.sqlite3"

    # 1. Execute
    res = execute_db_migration(db_path, catalog_file)
    assert res["status"] == "success"
    assert res["inserted"] == 26
    assert res["skipped"] == 0

    # Verify rows in DB
    with connect_db(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM class_assignments;").fetchone()[0]
        assert count == 26

    # 2. Re-run execute: should be idempotent (0 inserted, 26 skipped)
    res2 = execute_db_migration(db_path, catalog_file)
    assert res2["status"] == "success"
    assert res2["inserted"] == 0
    assert res2["skipped"] == 26

    with connect_db(db_path) as conn:
        count2 = conn.execute("SELECT COUNT(*) FROM class_assignments;").fetchone()[0]
        assert count2 == 26


def test_preexisting_conflict_preservation(tmp_path: Path, catalog_file: Path):
    db_path = tmp_path / "test_conflict.sqlite3"

    # Pre-seed one conflicting row with different topic_id
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO class_assignments (
                id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
            ) VALUES ('custom_id', 'ИС 25/9-3В', 'custom_subj', 'custom_topic', '2026-09-21', 1, 'ts', 'ts');
            """
        )
        conn.commit()

    # Execute migration
    res = execute_db_migration(db_path, catalog_file)
    assert res["inserted"] == 25
    assert res["skipped"] == 1

    # Verify custom row was not overwritten
    with connect_db(db_path) as conn:
        row = conn.execute(
            "SELECT id, topic_id FROM class_assignments WHERE group_name = 'ИС 25/9-3В' AND lesson_date = '2026-09-21' AND pair_number = 1;"
        ).fetchone()
        assert row["id"] == "custom_id"
        assert row["topic_id"] == "custom_topic"


def test_rollback_db_migration(tmp_path: Path, catalog_file: Path):
    db_path = tmp_path / "test_rollback.sqlite3"

    # Execute migration
    execute_db_migration(db_path, catalog_file)

    # Insert a non-historical custom assignment
    with connect_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO class_assignments (
                id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
            ) VALUES ('custom_other', 'Другая Группа', 's1', 't1', '2026-11-01', 1, 'ts', 'ts');
            """
        )
        conn.commit()

    # Rollback dry-run
    rb_dry = rollback_db_migration(db_path, dry_run=True)
    assert rb_dry["mode"] == "dry-run"
    assert rb_dry["would_delete"] == 26

    # Execute rollback
    rb_res = rollback_db_migration(db_path, dry_run=False)
    assert rb_res["status"] == "rolled_back"
    assert rb_res["deleted"] == 26

    # Verify only custom row remains
    with connect_db(db_path) as conn:
        rows = conn.execute("SELECT id FROM class_assignments;").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == "custom_other"


def test_cli_flags(tmp_path: Path, catalog_file: Path):
    db_path = tmp_path / "cli.sqlite3"

    # Dry run
    code_dry = main([
        "--db", str(db_path),
        "--catalog", str(catalog_file),
        "--dry-run",
    ])
    assert code_dry == 0
    assert not db_path.exists()

    # Execute
    code_exec = main([
        "--db", str(db_path),
        "--catalog", str(catalog_file),
        "--execute",
    ])
    assert code_exec == 0
    assert db_path.exists()

    # Rollback
    code_rb = main([
        "--db", str(db_path),
        "--catalog", str(catalog_file),
        "--rollback",
    ])
    assert code_rb == 0
    with connect_db(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM class_assignments;").fetchone()[0]
        assert count == 0
