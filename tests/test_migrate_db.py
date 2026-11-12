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
    candidates = [
        Path("catalog.json"),
        Path(__file__).resolve().parents[1] / "catalog.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    for c in candidates:
        example = c.with_name("catalog.example.json")
        if example.is_file():
            return example
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
        original_row = tuple(
            conn.execute(
                "SELECT id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at "
                "FROM class_assignments WHERE id = 'custom_id';"
            ).fetchone()
        )

    # Execute migration
    res = execute_db_migration(db_path, catalog_file)
    assert res["inserted"] == 25
    assert res["skipped"] == 1

    # Verify custom row was not overwritten and total count is 26
    with connect_db(db_path) as conn:
        row = conn.execute(
            "SELECT id, topic_id FROM class_assignments WHERE group_name = 'ИС 25/9-3В' AND lesson_date = '2026-09-21' AND pair_number = 1;"
        ).fetchone()
        assert row["id"] == "custom_id"
        assert row["topic_id"] == "custom_topic"
        total_rows = conn.execute("SELECT COUNT(*) FROM class_assignments;").fetchone()[0]
        assert total_rows == 26

    # Rollback dry-run: only historical assignments would be deleted (25), all 26 remain
    rb_dry = rollback_db_migration(db_path, dry_run=True)
    assert rb_dry["mode"] == "dry-run"
    assert rb_dry["would_delete"] == 25
    with connect_db(db_path) as conn:
        total_rows = conn.execute("SELECT COUNT(*) FROM class_assignments;").fetchone()[0]
        assert total_rows == 26

    # Real rollback: deletes 25 historical assignments
    rb_res = rollback_db_migration(db_path, dry_run=False)
    assert rb_res["status"] == "rolled_back"
    assert rb_res["deleted"] == 25

    # Verify only custom row remains, matching original values exactly
    with connect_db(db_path) as conn:
        rows = conn.execute(
            "SELECT id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at "
            "FROM class_assignments;"
        ).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0]) == original_row

    # Repeated rollback removes 0 rows
    rb_repeat = rollback_db_migration(db_path, dry_run=False)
    assert rb_repeat["status"] == "rolled_back"
    assert rb_repeat["deleted"] == 0
    with connect_db(db_path) as conn:
        total_rows = conn.execute("SELECT COUNT(*) FROM class_assignments;").fetchone()[0]
        assert total_rows == 1


def test_rollback_without_execute_preserves_custom_row(tmp_path: Path):
    db_path = tmp_path / "test_no_exec_conflict.sqlite3"

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
        original_row = tuple(
            conn.execute(
                "SELECT id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at "
                "FROM class_assignments WHERE id = 'custom_id';"
            ).fetchone()
        )

    # Dry rollback on custom row in historical slot without execute
    rb_dry = rollback_db_migration(db_path, dry_run=True)
    assert rb_dry["mode"] == "dry-run"
    assert rb_dry["would_delete"] == 0

    # Real rollback on custom row in historical slot without execute
    rb_res = rollback_db_migration(db_path, dry_run=False)
    assert rb_res["status"] == "rolled_back"
    assert rb_res["deleted"] == 0

    with connect_db(db_path) as conn:
        rows = conn.execute(
            "SELECT id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at "
            "FROM class_assignments;"
        ).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0]) == original_row


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

    # Default preview (no mode flags)
    code_default = main([
        "--db", str(db_path),
        "--catalog", str(catalog_file),
    ])
    assert code_default == 0
    assert not db_path.exists()

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
    with connect_db(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM class_assignments;").fetchone()[0]
        assert count == 26

    # Rollback dry run after execute: leaves all rows and data intact
    with connect_db(db_path) as conn:
        rows_before = [tuple(r) for r in conn.execute("SELECT * FROM class_assignments ORDER BY id;").fetchall()]
        assert len(rows_before) == 26

    code_rb_dry1 = main([
        "--db", str(db_path),
        "--catalog", str(catalog_file),
        "--rollback",
        "--dry-run",
    ])
    assert code_rb_dry1 == 0
    with connect_db(db_path) as conn:
        rows_after1 = [tuple(r) for r in conn.execute("SELECT * FROM class_assignments ORDER BY id;").fetchall()]
        assert rows_after1 == rows_before

    code_rb_dry2 = main([
        "--db", str(db_path),
        "--catalog", str(catalog_file),
        "--dry-run",
        "--rollback",
    ])
    assert code_rb_dry2 == 0
    with connect_db(db_path) as conn:
        rows_after2 = [tuple(r) for r in conn.execute("SELECT * FROM class_assignments ORDER BY id;").fetchall()]
        assert rows_after2 == rows_before
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


CONFLICTING_CLI_FLAGS = [
    ["--execute", "--dry-run"],
    ["--dry-run", "--execute"],
    ["--execute", "--rollback"],
    ["--rollback", "--execute"],
    ["--execute", "--rollback", "--dry-run"],
    ["--execute", "--dry-run", "--rollback"],
    ["--rollback", "--execute", "--dry-run"],
    ["--rollback", "--dry-run", "--execute"],
    ["--dry-run", "--execute", "--rollback"],
    ["--dry-run", "--rollback", "--execute"],
]


@pytest.mark.parametrize("conflict_flags", CONFLICTING_CLI_FLAGS)
def test_cli_conflicting_flags_db_nonexistent(
    tmp_path: Path, catalog_file: Path, conflict_flags: list[str], capsys: pytest.CaptureFixture
):
    db_path = tmp_path / "nonexistent.sqlite3"
    with pytest.raises(SystemExit) as exc_info:
        main([
            "--db", str(db_path),
            "--catalog", str(catalog_file),
            *conflict_flags,
        ])
    assert exc_info.value.code == 2
    assert not db_path.exists()
    captured = capsys.readouterr()
    assert "error:" in captured.err.lower()
    assert ("--execute cannot be combined with --dry-run" in captured.err) or ("not allowed with argument" in captured.err)

@pytest.mark.parametrize("conflict_flags", CONFLICTING_CLI_FLAGS)
def test_cli_conflicting_flags_db_existing_control_row(
    tmp_path: Path, catalog_file: Path, conflict_flags: list[str], capsys: pytest.CaptureFixture
):
    db_path = tmp_path / "control.sqlite3"
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO class_assignments (
                id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
            ) VALUES ('ctrl_id', 'ИС 25/9-3В', 'ctrl_subj', 'ctrl_topic', '2026-09-01', 1, 'ts', 'ts');
            """
        )
        conn.commit()
        original_row = tuple(
            conn.execute("SELECT * FROM class_assignments WHERE id = 'ctrl_id';").fetchone()
        )

    with pytest.raises(SystemExit) as exc_info:
        main([
            "--db", str(db_path),
            "--catalog", str(catalog_file),
            *conflict_flags,
        ])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err.lower()
    assert ("--execute cannot be combined with --dry-run" in captured.err) or ("not allowed with argument" in captured.err)

    with connect_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM class_assignments;").fetchall()
        assert len(rows) == 1
        assert tuple(rows[0]) == original_row

@pytest.mark.parametrize("conflict_flags", CONFLICTING_CLI_FLAGS)
def test_cli_conflicting_flags_db_already_migrated(
    tmp_path: Path, catalog_file: Path, conflict_flags: list[str], capsys: pytest.CaptureFixture
):
    db_path = tmp_path / "migrated.sqlite3"
    execute_db_migration(db_path, catalog_file)
    with connect_db(db_path) as conn:
        rows_before = [tuple(r) for r in conn.execute("SELECT * FROM class_assignments ORDER BY id;").fetchall()]
        assert len(rows_before) == 26

    with pytest.raises(SystemExit) as exc_info:
        main([
            "--db", str(db_path),
            "--catalog", str(catalog_file),
            *conflict_flags,
        ])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err.lower()
    assert ("not allowed with argument" in captured.err) or ("--execute cannot be combined with --dry-run" in captured.err)

    with connect_db(db_path) as conn:
        rows_after = [tuple(r) for r in conn.execute("SELECT * FROM class_assignments ORDER BY id;").fetchall()]
        assert rows_after == rows_before

def test_rollback_db_migration_skipped_nonexistent_db_contract(tmp_path: Path):
    db_path = tmp_path / "deeply" / "nested" / "nonexistent.sqlite3"

    # dry_run=True must include mode='dry-run' and would_delete=0
    dry_res = rollback_db_migration(db_path, dry_run=True)
    assert dry_res["status"] == "skipped"
    assert dry_res["deleted"] == 0
    assert dry_res["mode"] == "dry-run"
    assert dry_res["would_delete"] == 0
    assert "Database file does not exist" in dry_res["reason"]
    assert not db_path.exists()
    assert not db_path.parent.exists()

    # dry_run=False retains original skipped result
    normal_res = rollback_db_migration(db_path, dry_run=False)
    assert normal_res["status"] == "skipped"
    assert normal_res["deleted"] == 0
    assert "would_delete" not in normal_res
    assert "mode" not in normal_res
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_rollback_db_migration_skipped_existing_db_without_table_contract(tmp_path: Path):
    db_path = tmp_path / "custom.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE custom_data (id INTEGER PRIMARY KEY, note TEXT);")
        conn.execute("INSERT INTO custom_data (id, note) VALUES (1, 'keep');")
        conn.commit()

    # dry_run=True must include mode='dry-run' and would_delete=0
    dry_res = rollback_db_migration(db_path, dry_run=True)
    assert dry_res["status"] == "skipped"
    assert dry_res["deleted"] == 0
    assert dry_res["mode"] == "dry-run"
    assert dry_res["would_delete"] == 0
    assert "class_assignments" in dry_res["reason"]

    # verify custom table intact and class_assignments not created
    with sqlite3.connect(db_path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        assert "custom_data" in tables
        assert "class_assignments" not in tables
        rows = conn.execute("SELECT * FROM custom_data;").fetchall()
        assert rows == [(1, "keep")]

    # dry_run=False retains original skipped result
    normal_res = rollback_db_migration(db_path, dry_run=False)
    assert normal_res["status"] == "skipped"
    assert normal_res["deleted"] == 0
    assert "would_delete" not in normal_res
    assert "mode" not in normal_res


@pytest.mark.parametrize("dry_run_args", [
    ["--rollback", "--dry-run"],
    ["--dry-run", "--rollback"],
])
def test_main_rollback_preview_skipped_nonexistent_db_cli(
    tmp_path: Path, catalog_file: Path, dry_run_args: list[str], capsys: pytest.CaptureFixture
):
    db_path = tmp_path / "missing_parent" / "missing.sqlite3"
    code = main([
        "--db", str(db_path),
        "--catalog", str(catalog_file),
        *dry_run_args,
    ])
    assert code == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert "[DRY RUN] Skipped rollback: Database file does not exist:" in captured.out
    assert not db_path.exists()
    assert not db_path.parent.exists()


@pytest.mark.parametrize("dry_run_args", [
    ["--rollback", "--dry-run"],
    ["--dry-run", "--rollback"],
])
def test_main_rollback_preview_skipped_existing_db_without_table_cli(
    tmp_path: Path, catalog_file: Path, dry_run_args: list[str], capsys: pytest.CaptureFixture
):
    db_path = tmp_path / "no_table.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO users VALUES (42, 'alice');")
        conn.commit()

    code = main([
        "--db", str(db_path),
        "--catalog", str(catalog_file),
        *dry_run_args,
    ])
    assert code == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert "[DRY RUN] Skipped rollback: Table class_assignments does not exist" in captured.out

    with sqlite3.connect(db_path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        assert "users" in tables
        assert "class_assignments" not in tables
        rows = conn.execute("SELECT * FROM users;").fetchall()
        assert rows == [(42, "alice")]

