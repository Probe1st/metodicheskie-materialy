"""Tests for SQLite v2 database module and schema constraints."""

from __future__ import annotations

import concurrent.futures
import os
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from app.database import (
    DatabaseError,
    DatabaseNotFoundError,
    DatabaseValidationError,
    connect,
    create_schema,
    initialize,
    normalize_name_key,
    validate,
    validate_lesson_date,
    validate_pair_number,
)


def _seed_base_data(conn: sqlite3.Connection):
    """Seed group, subject, and topic for constraint tests."""
    group_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO groups (id, name, name_key) VALUES (?, ?, ?);",
        (group_id, "Группа-101", normalize_name_key("Группа-101")),
    )

    subject_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO subjects (id, name, name_key, is_system) VALUES (?, ?, ?, 0);",
        (subject_id, "Информатика", normalize_name_key("Информатика")),
    )

    topic_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO topics (id, subject_id, name, name_key) VALUES (?, ?, ?, ?);",
        (topic_id, subject_id, "Тема-1", normalize_name_key("Тема-1")),
    )
    return group_id, subject_id, topic_id


# --- 0. Exceptions Hierarchy ---

def test_exception_hierarchy():
    assert issubclass(DatabaseValidationError, DatabaseError)
    assert issubclass(DatabaseNotFoundError, DatabaseValidationError)
    assert issubclass(DatabaseNotFoundError, FileNotFoundError)


# --- 1. Connection Context Manager ---

def test_connect_row_factory(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        conn.execute("CREATE TABLE t (id TEXT, val INTEGER);")
        conn.execute("INSERT INTO t VALUES ('a', 42);")

    with connect(db_path) as conn:
        row = conn.execute("SELECT id, val FROM t;").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["id"] == "a"
        assert row["val"] == 42
        assert row[0] == "a"
        assert row[1] == 42


def test_connect_foreign_keys_and_busy_timeout(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1
        bt = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert bt == 5000


def test_connect_commits_on_clean_exit(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        conn.execute("CREATE TABLE items (name TEXT);")
        conn.execute("INSERT INTO items VALUES ('item1');")

    with connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM items;").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "item1"


def test_connect_rolls_back_on_exception(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        conn.execute("CREATE TABLE items (name TEXT);")
        conn.execute("INSERT INTO items VALUES ('initial');")

    with pytest.raises(RuntimeError, match="abort"), connect(db_path) as conn:
        conn.execute("INSERT INTO items VALUES ('uncommitted');")
        raise RuntimeError("abort")

    with connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM items;").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "initial"


# --- 2. Initialize Function ---

def test_initialize_creates_database_and_system_subject(tmp_path: Path):
    db_path = tmp_path / "nested" / "v2.db"
    initialize(db_path)

    assert db_path.is_file()

    # POSIX permissions check
    if os.name == "posix":
        assert oct(db_path.stat().st_mode & 0o777) == "0o600"
        assert oct(db_path.parent.stat().st_mode & 0o777) == "0o700"

    with connect(db_path) as conn:
        # Check user_version
        user_version = conn.execute("PRAGMA user_version;").fetchone()[0]
        assert user_version == 2

        # Check journal_mode
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"

        # Check system subject
        rows = conn.execute(
            "SELECT * FROM subjects WHERE is_system = 1;"
        ).fetchall()
        assert len(rows) == 1
        sys_subj = rows[0]
        assert sys_subj["name"] == "Без предмета"
        assert sys_subj["name_key"] == "без предмета"
        assert sys_subj["archived_at"] is None
        assert sys_subj["is_system"] == 1
        # Canonical UUID validation
        uuid.UUID(sys_subj["id"])


def test_initialize_fails_if_database_already_exists(tmp_path: Path):
    db_path = tmp_path / "existing.db"
    db_path.touch()

    with pytest.raises(FileExistsError, match="Database already exists"):
        initialize(db_path)


def test_concurrent_initialize(tmp_path: Path):
    target_db = tmp_path / "concurrent.db"
    thread_count = 10
    barrier = threading.Barrier(thread_count)

    def worker():
        barrier.wait(timeout=5)
        initialize(target_db)

    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = [executor.submit(worker) for _ in range(thread_count)]
        successes = 0
        file_exists_errors = 0
        for future in futures:
            try:
                future.result()
                successes += 1
            except FileExistsError:
                file_exists_errors += 1

    assert successes == 1
    assert file_exists_errors == thread_count - 1
    validate(target_db)

def test_initialize_cleans_up_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "failed.db"

    def fail_schema(conn: sqlite3.Connection):
        raise RuntimeError("schema creation failed")

    monkeypatch.setattr("app.database.create_schema", fail_schema)

    with pytest.raises(RuntimeError, match="schema creation failed"):
        initialize(db_path)

    assert not db_path.exists()

# --- 3. Validate Function ---

def test_validate_succeeds_on_initialized_db(tmp_path: Path):
    db_path = tmp_path / "valid.db"
    initialize(db_path)
    validate(db_path)  # Should not raise


def test_validate_fails_non_existent_file(tmp_path: Path):
    non_existent = tmp_path / "missing.db"
    with pytest.raises((DatabaseValidationError, FileNotFoundError)):
        validate(non_existent)


def test_validate_fails_old_user_version(tmp_path: Path):
    db_path = tmp_path / "old.db"
    with connect(db_path) as conn:
        create_schema(conn)
        conn.execute("PRAGMA user_version = 1;")

    with pytest.raises(DatabaseValidationError, match="user_version"):
        validate(db_path)


def test_validate_fails_missing_tables(tmp_path: Path):
    db_path = tmp_path / "incomplete.db"
    with connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 2;")
        conn.execute("CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT, name_key TEXT);")

    with pytest.raises(DatabaseValidationError, match="Missing required tables"):
        validate(db_path)


def test_validate_fails_corrupt_database(tmp_path: Path):
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"NOT A SQLITE DATABASE" * 20)

    with pytest.raises(DatabaseValidationError):
        validate(db_path)


# --- 4. Normalization and Validation Helpers ---

def test_normalize_name_key():
    assert normalize_name_key("  Math 101  ") == "math 101"
    assert normalize_name_key("Информатика") == "информатика"
    assert normalize_name_key("  ПрОгРаМмИрОвАнИе  ") == "программирование"

    # NFC normalization check
    decomposed = "e\u0301"  # 'e' + combining acute accent
    composed = "\u00e9"     # 'é'
    assert normalize_name_key(decomposed) == normalize_name_key(composed)

    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_name_key("")

    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_name_key("   \t\n  ")

    with pytest.raises(TypeError):
        normalize_name_key(None)  # type: ignore[arg-type]


def test_validate_lesson_date():
    assert validate_lesson_date("2026-11-03") == "2026-11-03"
    assert validate_lesson_date("2024-02-29") == "2024-02-29"  # leap year

    with pytest.raises(ValueError, match="Invalid lesson date format"):
        validate_lesson_date("2026/11/03")

    with pytest.raises(ValueError, match="Invalid lesson date format"):
        validate_lesson_date("03-11-2026")

    with pytest.raises(ValueError, match="Invalid lesson date format"):
        validate_lesson_date("2026-1-3")

    with pytest.raises(ValueError, match="Invalid calendar date"):
        validate_lesson_date("2026-02-29")  # not leap year

    with pytest.raises(ValueError, match="Invalid calendar date"):
        validate_lesson_date("2026-13-01")

    with pytest.raises(ValueError, match="Invalid calendar date"):
        validate_lesson_date("2026-04-31")

    with pytest.raises(TypeError):
        validate_lesson_date(20261103)  # type: ignore[arg-type]


def test_validate_pair_number():
    for p in range(1, 7):
        assert validate_pair_number(p) == p

    with pytest.raises(ValueError, match="between 1 and 6"):
        validate_pair_number(0)

    with pytest.raises(ValueError, match="between 1 and 6"):
        validate_pair_number(7)

    with pytest.raises(TypeError):
        validate_pair_number(True)  # bool must be rejected

    with pytest.raises(TypeError):
        validate_pair_number(False)

    with pytest.raises(TypeError):
        validate_pair_number("3")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        validate_pair_number(2.0)  # type: ignore[arg-type]


# --- 5. Schema Constraints Tests ---

def test_schema_single_system_subject_constraint(tmp_path: Path):
    db_path = tmp_path / "schema.db"
    initialize(db_path)

    with connect(db_path) as conn:
        # Attempt to insert a second system subject
        second_sys_id = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO subjects (id, name, name_key, is_system, archived_at)
                VALUES (?, 'System 2', 'system 2', 1, NULL);
                """,
                (second_sys_id,),
            )

        # Inserting regular subjects (is_system=0) should succeed
        conn.execute(
            """
            INSERT INTO subjects (id, name, name_key, is_system, archived_at)
            VALUES (?, 'Regular 1', 'regular 1', 0, NULL);
            """,
            (str(uuid.uuid4()),),
        )
        conn.execute(
            """
            INSERT INTO subjects (id, name, name_key, is_system, archived_at)
            VALUES (?, 'Regular 2', 'regular 2', 0, NULL);
            """,
            (str(uuid.uuid4()),),
        )


def test_schema_system_subject_cannot_be_archived(tmp_path: Path):
    db_path = tmp_path / "schema.db"
    initialize(db_path)

    with connect(db_path) as conn:
        sys_subj = conn.execute("SELECT id FROM subjects WHERE is_system = 1;").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE subjects SET archived_at = '2026-11-03T10:00:00Z' WHERE id = ?;",
                (sys_subj["id"],),
            )

        # Archiving regular subject succeeds
        reg_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO subjects (id, name, name_key, is_system, archived_at) VALUES (?, 'Reg', 'reg', 0, NULL);",
            (reg_id,),
        )
        conn.execute(
            "UPDATE subjects SET archived_at = '2026-11-03T10:00:00Z' WHERE id = ?;",
            (reg_id,),
        )


def test_schema_composite_fk_student_group_mismatch(tmp_path: Path):
    db_path = tmp_path / "schema.db"
    initialize(db_path)

    with connect(db_path) as conn:
        g1_id, _, topic_id = _seed_base_data(conn)

        # Group 2 and student in Group 2
        g2_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO groups (id, name, name_key) VALUES (?, 'Группа-2', 'группа-2');",
            (g2_id,),
        )
        s2_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, 'Студент 2');",
            (s2_id, g2_id),
        )

        # Attempt to insert submission referencing student s2 (from g2), but claiming group g1
        subm_id = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, attempt_number, created_at)
                VALUES (?, ?, ?, ?, 1, '2026-11-03T10:00:00Z');
                """,
                (subm_id, s2_id, g1_id, topic_id),
            )


def test_schema_composite_fk_assignment_group_topic_mismatch(tmp_path: Path):
    db_path = tmp_path / "schema.db"
    initialize(db_path)

    with connect(db_path) as conn:
        g1_id, sub_id, t1_id = _seed_base_data(conn)

        # Create student in Group 1
        st_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, 'Студент 1');",
            (st_id, g1_id),
        )

        # Create topic 2 in the same subject
        t2_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO topics (id, subject_id, name, name_key) VALUES (?, ?, 'Тема-2', 'тема-2');",
            (t2_id, sub_id),
        )

        # Create class assignment for (g1_id, t1_id)
        asgn_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
            VALUES (?, ?, ?, '2026-11-03', 1, 'now', 'now');
            """,
            (asgn_id, g1_id, t1_id),
        )

        # Submission referencing assignment asgn_id but claiming topic t2_id must fail composite FK
        subm_id = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, created_at)
                VALUES (?, ?, ?, ?, ?, 1, 'now');
                """,
                (subm_id, st_id, g1_id, t2_id, asgn_id),
            )

        # Valid submission matching (asgn_id, g1_id, t1_id) succeeds
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, created_at)
            VALUES (?, ?, ?, ?, ?, 1, 'now');
            """,
            (subm_id, st_id, g1_id, t1_id, asgn_id),
        )


def test_schema_active_assignment_slot_uniqueness(tmp_path: Path):
    db_path = tmp_path / "schema.db"
    initialize(db_path)

    with connect(db_path) as conn:
        g1_id, _, t1_id = _seed_base_data(conn)
        a1_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
            VALUES (?, ?, ?, '2026-11-03', 2, 'now', 'now');
            """,
            (a1_id, g1_id, t1_id),
        )

        # Conflicting active assignment in the same slot
        a2_id = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                VALUES (?, ?, ?, '2026-11-03', 2, 'now', 'now');
                """,
                (a2_id, g1_id, t1_id),
            )

        # Archive a1_id
        conn.execute(
            "UPDATE class_assignments SET archived_at = '2026-11-03T11:00:00Z' WHERE id = ?;",
            (a1_id,),
        )

        # Now inserting into the same slot succeeds
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
            VALUES (?, ?, ?, '2026-11-03', 2, 'now', 'now');
            """,
            (a2_id, g1_id, t1_id),
        )


def test_schema_submission_attempt_uniqueness(tmp_path: Path):
    db_path = tmp_path / "schema.db"
    initialize(db_path)

    with connect(db_path) as conn:
        g1_id, _, t1_id = _seed_base_data(conn)
        st_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, 'Студент');",
            (st_id, g1_id),
        )

        subm1_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, attempt_number, created_at)
            VALUES (?, ?, ?, ?, 1, 'now');
            """,
            (subm1_id, st_id, g1_id, t1_id),
        )

        # Duplicate attempt 1 for same student and topic fails
        subm2_id = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, attempt_number, created_at)
                VALUES (?, ?, ?, ?, 1, 'now');
                """,
                (subm2_id, st_id, g1_id, t1_id),
            )

        # Attempt 2 succeeds
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, attempt_number, created_at)
            VALUES (?, ?, ?, ?, 2, 'now');
            """,
            (subm2_id, st_id, g1_id, t1_id),
        )


def test_schema_checks_and_field_constraints(tmp_path: Path):
    db_path = tmp_path / "schema.db"
    initialize(db_path)

    with connect(db_path) as conn:
        g1_id, _, t1_id = _seed_base_data(conn)

        # Empty display name rejected
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, '   ');",
                (str(uuid.uuid4()), g1_id),
            )

        st_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, 'Иван');",
            (st_id, g1_id),
        )

        # Pair number check (pair_number BETWEEN 1 AND 6)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                VALUES (?, ?, ?, '2026-11-03', 0, 'now', 'now');
                """,
                (str(uuid.uuid4()), g1_id, t1_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                VALUES (?, ?, ?, '2026-11-03', 7, 'now', 'now');
                """,
                (str(uuid.uuid4()), g1_id, t1_id),
            )

        # Lesson date format and calendar validity checks
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                VALUES (?, ?, ?, '03-11-2026', 1, 'now', 'now');
                """,
                (str(uuid.uuid4()), g1_id, t1_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                VALUES (?, ?, ?, 'abcd-ef-gh', 1, 'now', 'now');
                """,
                (str(uuid.uuid4()), g1_id, t1_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                VALUES (?, ?, ?, '2026-02-30', 1, 'now', 'now');
                """,
                (str(uuid.uuid4()), g1_id, t1_id),
            )

        # Attempt number >= 1 check
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, attempt_number, created_at)
                VALUES (?, ?, ?, ?, 0, 'now');
                """,
                (str(uuid.uuid4()), st_id, g1_id, t1_id),
            )

        # Review status: pending with reviewed_at set must fail
        subm_id = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, attempt_number, review_status, reviewed_at, created_at)
                VALUES (?, ?, ?, ?, 1, 'pending', '2026-11-03T10:00:00Z', 'now');
                """,
                (subm_id, st_id, g1_id, t1_id),
            )

        # Review status valid
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, attempt_number, review_status, reviewed_at, created_at)
            VALUES (?, ?, ?, ?, 1, 'reviewed', '2026-11-03T10:00:00Z', 'now');
            """,
            (subm_id, st_id, g1_id, t1_id),
        )

        # Submission file size >= 0
        file_id = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes)
                VALUES (?, ?, 'doc.pdf', '/path/doc.pdf', -1);
                """,
                (file_id, subm_id),
            )

        # Submission file valid
        conn.execute(
            """
            INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes)
            VALUES (?, ?, 'doc.pdf', '/path/doc.pdf', 1024);
            """,
            (file_id, subm_id),
        )

        # Submission file 1-to-1: second file for same submission fails UNIQUE(submission_id)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes)
                VALUES (?, ?, 'doc2.pdf', '/path/doc2.pdf', 2048);
                """,
                (str(uuid.uuid4()), subm_id),
            )
