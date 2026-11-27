"""Audit test suite for database contract families DB-01 through DB-06.

Validates schema constraints, concurrency, connection semantics, isolation,
validation fingerprints, foreign keys, CHECK constraints, and Unicode normalization.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from app.database import (
    CURRENT_SCHEMA_VERSION,
    REQUIRED_TABLES,
    DatabaseError,
    DatabaseNotFoundError,
    DatabaseValidationError,
    connect,
    initialize,
    normalize_name_key,
    validate,
    validate_lesson_date,
    validate_pair_number,
)
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_DATE,
    A1_ID,
    A1_PAIR,
    COLLISION_TOPIC_NAME,
    G1_ID,
    G1_NAME,
    G2_ID,
    G2_NAME,
    S1_ID,
    S2_ID,
    STUDENT1_ID,
    T1_ID,
    T2_ID,
    canonical_uuid,
    read_db_state,
)

# ===========================================================================
# DB-01: Initialization & Concurrency
# ===========================================================================

@pytest.mark.parametrize("case_id", ["DB-01-01"])
def test_db_01_01_fresh_initialization(case_id: str, audit_env: AuditEnv) -> None:
    """DB-01-01: Fresh initialization must create unified SQLite v2 schema with WAL mode and 1 system subject."""
    db_path = audit_env.private / "fresh.sqlite3"
    assert not db_path.exists()

    initialize(db_path)
    assert db_path.is_file()

    # Permissions check: 0o600 for file, 0o700 for parent
    st = db_path.stat()
    assert (st.st_mode & 0o777) == 0o600

    state = read_db_state(db_path)
    assert state["user_version"] == CURRENT_SCHEMA_VERSION
    assert state["integrity_check"] == ["ok"]
    assert state["foreign_key_check"] == []

    # Check required tables
    for tbl in REQUIRED_TABLES:
        assert tbl in state["tables"], f"Missing table {tbl}"

    # Journal mode check
    with sqlite3.connect(str(db_path)) as raw_conn:
        cur = raw_conn.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0]
        assert journal_mode.lower() == "wal"

    # Exactly 1 system subject
    subjects = state["tables"]["subjects"]["rows"]
    system_subjects = [s for s in subjects if s["is_system"] == 1]
    assert len(system_subjects) == 1
    assert system_subjects[0]["name"] == "Без предмета"
    assert system_subjects[0]["archived_at"] is None


@pytest.mark.parametrize("case_id", ["DB-01-02"])
def test_db_01_02_reinitialization_conflict(case_id: str, audit_env: AuditEnv) -> None:
    """DB-01-02: Repeated initialize on existing file raises FileExistsError without overwriting data."""
    db_path = audit_env.populate_canonical()
    state_before = read_db_state(db_path)
    original_size = db_path.stat().st_size
    assert original_size > 0

    with pytest.raises(FileExistsError, match="Database already exists"):
        initialize(db_path)

    # Data and size should be untouched
    state_after = read_db_state(db_path)
    assert state_after["tables"]["groups"]["row_count"] == state_before["tables"]["groups"]["row_count"]
    assert state_after["tables"]["students"]["row_count"] == state_before["tables"]["students"]["row_count"]


@pytest.mark.parametrize("case_id", ["DB-01-03"])
def test_db_01_03_create_schema_failure_cleanup(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB-01-03: Failure during schema creation cleans up partial file and leaves no corrupt DB."""
    db_path = audit_env.private / "failed_init.sqlite3"

    def faulty_create_schema(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE partial_table (id INT);")
        raise RuntimeError("Simulated DDL disk/schema failure")

    monkeypatch.setattr("app.database.create_schema", faulty_create_schema)

    with pytest.raises(RuntimeError, match="Simulated DDL disk/schema failure"):
        initialize(db_path)

    # Must be unlinked atomically on failure
    assert not db_path.exists(), "Partial database file was not cleaned up after failure"


@pytest.mark.parametrize("case_id", ["DB-01-04"])
def test_db_01_04_concurrent_initialization_threads_and_processes(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-01-04: Concurrent threads and processes racing to initialize exact same path."""
    # 1. 10 Threads racing
    target_thread_db = audit_env.private / "race_threads.sqlite3"
    results_thread: list[str] = []

    def thread_init() -> None:
        try:
            initialize(target_thread_db)
            results_thread.append("SUCCESS")
        except FileExistsError:
            results_thread.append("FILE_EXISTS")
        except OSError as e:
            results_thread.append(f"UNEXPECTED: {type(e).__name__}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(thread_init) for _ in range(10)]
        concurrent.futures.wait(futures)

    assert results_thread.count("SUCCESS") == 1
    assert results_thread.count("FILE_EXISTS") == 9
    assert results_thread.count("SUCCESS") + results_thread.count("FILE_EXISTS") == 10

    # Verify the created database is completely valid
    validate(target_thread_db)
    state_thread = read_db_state(target_thread_db)
    assert state_thread["user_version"] == CURRENT_SCHEMA_VERSION
    assert len([s for s in state_thread["tables"]["subjects"]["rows"] if s["is_system"] == 1]) == 1

    # 2. 4 Separate OS Processes racing
    target_proc_db = audit_env.private / "race_processes.sqlite3"
    target_proc_str = str(target_proc_db)
    hw_root = str(Path(__file__).resolve().parents[2])
    script = f"""
import sys
sys.path.insert(0, {hw_root!r})
from app.database import initialize
try:
    initialize({target_proc_str!r})
    sys.exit(0)
except FileExistsError:
    sys.exit(42)
except OSError:
    sys.exit(99)
"""
    procs = [
        subprocess.Popen([sys.executable, "-c", script], cwd=str(audit_env.root))
        for _ in range(4)
    ]
    exit_codes = [p.wait() for p in procs]

    assert exit_codes.count(0) == 1, f"Expected exactly 1 success process, got {exit_codes}"
    assert exit_codes.count(42) == 3, f"Expected 3 FileExistsError exit codes, got {exit_codes}"

    validate(target_proc_db)
    state_proc = read_db_state(target_proc_db)
    assert state_proc["user_version"] == CURRENT_SCHEMA_VERSION
    assert len([s for s in state_proc["tables"]["subjects"]["rows"] if s["is_system"] == 1]) == 1


# ===========================================================================
# DB-02: Connection Management & Transactions
# ===========================================================================

@pytest.mark.parametrize("case_id", ["DB-02-01"])
def test_db_02_01_connect_pragmas_and_context(case_id: str, audit_env: AuditEnv) -> None:
    """DB-02-01: connect() context manager sets row_factory, foreign_keys=ON, busy_timeout=5000."""
    db_path = audit_env.init_db()

    with connect(db_path) as conn:
        assert conn.row_factory == sqlite3.Row

        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1, "foreign_keys PRAGMA was not enabled"

        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert timeout == 5000, f"Expected busy_timeout=5000, got {timeout}"

        # Test row_factory behavior
        conn.execute(
            "INSERT INTO groups (id, name, name_key) VALUES (?, ?, ?);",
            (str(G1_ID), G1_NAME, normalize_name_key(G1_NAME)),
        )
        row = conn.execute("SELECT id, name FROM groups WHERE id = ?;", (str(G1_ID),)).fetchone()
        assert row["name"] == G1_NAME
        assert row[1] == G1_NAME


@pytest.mark.parametrize("case_id", ["DB-02-02"])
def test_db_02_02_lock_timeout_concurrency(case_id: str, audit_env: AuditEnv) -> None:
    """DB-02-02: Busy timeout behavior when write lock is held by concurrent connection."""
    db_path = audit_env.init_db()

    # Part A: Connection 2 times out when lock is held longer than busy_timeout
    with connect(db_path) as conn1:
        conn1.execute("BEGIN IMMEDIATE;")
        with connect(db_path) as conn2:
            # Set small busy timeout to keep audit test fast
            conn2.execute("PRAGMA busy_timeout = 200;")
            t0 = time.time()
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                conn2.execute("BEGIN IMMEDIATE;")
            elapsed = time.time() - t0
            assert elapsed >= 0.18, f"Timed out too early: {elapsed}s"
        conn1.execute("ROLLBACK;")

    # Part B: Connection 2 succeeds when lock is released within timeout
    ready_event = threading.Event()

    def lock_holder() -> None:
        thread_conn = sqlite3.connect(str(db_path))
        thread_conn.execute("BEGIN IMMEDIATE;")
        ready_event.set()
        time.sleep(0.15)
        thread_conn.commit()
        thread_conn.close()

    t = threading.Thread(target=lock_holder)
    t.start()
    ready_event.wait()

    with connect(db_path) as conn_waiting:
        # Default busy_timeout is 5000ms, holder releases in 150ms
        conn_waiting.execute("BEGIN IMMEDIATE;")
        conn_waiting.execute(
            "INSERT INTO groups (id, name, name_key) VALUES (?, ?, ?);",
            (str(G1_ID), G1_NAME, normalize_name_key(G1_NAME)),
        )

    t.join()


@pytest.mark.parametrize("case_id", ["DB-02-03"])
def test_db_02_03_transaction_isolation_and_rollback(case_id: str, audit_env: AuditEnv) -> None:
    """DB-02-03: Uncommitted writes invisible to outside readers; exceptions cause rollback."""
    db_path = audit_env.init_db()

    # Outside reader cannot see uncommitted changes
    with connect(db_path) as conn1:
        conn1.execute(
            "INSERT INTO groups (id, name, name_key) VALUES (?, ?, ?);",
            (str(G1_ID), G1_NAME, normalize_name_key(G1_NAME)),
        )
        # Separate reader connection
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as ro_conn:
            ro_cur = ro_conn.execute("SELECT count(*) FROM groups;")
            assert ro_cur.fetchone()[0] == 0, "Uncommitted insert was visible to external reader"

    # Now that conn1 exited normally, changes are committed
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as ro_conn:
        ro_cur = ro_conn.execute("SELECT count(*) FROM groups;")
        assert ro_cur.fetchone()[0] == 1

    # Test automatic rollback on error inside with connect
    with pytest.raises(RuntimeError, match="Fail inside transaction"), connect(db_path) as conn2:
        conn2.execute(
            "INSERT INTO groups (id, name, name_key) VALUES (?, ?, ?);",
            (str(G2_ID), G2_NAME, normalize_name_key(G2_NAME)),
        )
        raise RuntimeError("Fail inside transaction")

    with connect(db_path) as conn3:
        g2_count = conn3.execute("SELECT count(*) FROM groups WHERE id = ?;", (str(G2_ID),)).fetchone()[0]
        assert g2_count == 0, "Failed transaction was not rolled back"


# ===========================================================================
# DB-03: Validation & Schema Fingerprint
# ===========================================================================

@pytest.mark.parametrize("case_id", ["DB-03-01"])
def test_db_03_01_validate_missing_file(case_id: str, audit_env: AuditEnv) -> None:
    """DB-03-01: validate() raises DatabaseNotFoundError when database file does not exist."""
    missing = audit_env.private / "nonexistent.sqlite3"
    assert not missing.exists()

    with pytest.raises(DatabaseNotFoundError, match="Database file not found") as exc_info:
        validate(missing)
    assert isinstance(exc_info.value, FileNotFoundError)


@pytest.mark.parametrize(
    ("case_id", "filename", "corrupt_data", "user_version", "match_str"),
    [
        ("DB-03-02", "zero_byte.sqlite3", b"", None, "Unsupported database user_version"),
        ("DB-03-02", "garbage.sqlite3", b"GARBAGE_NOT_A_SQLITE_FILE", None, "Could not open database file|file is not a database"),
        ("DB-03-02", "old_version.sqlite3", None, 1, f"Unsupported database user_version: 1, expected {CURRENT_SCHEMA_VERSION}"),
    ],
)
def test_db_03_02_validate_invalid_files(
    case_id: str,
    filename: str,
    corrupt_data: bytes | None,
    user_version: int | None,
    match_str: str,
    audit_env: AuditEnv,
) -> None:
    """DB-03-02: validate() rejects zero-byte, corrupt, or obsolete user_version files."""
    target = audit_env.private / filename
    if corrupt_data is not None:
        target.write_bytes(corrupt_data)
    elif user_version is not None:
        with sqlite3.connect(str(target)) as conn:
            conn.execute(f"PRAGMA user_version = {user_version};")

    with pytest.raises(DatabaseValidationError, match=match_str):
        validate(target)


@pytest.mark.parametrize("case_id", ["DB-03-03"])
def test_db_03_03_validate_schema_fingerprint_and_system_subjects(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-03-03: validate() vs independent schema fingerprint.

    Audits behavior when tables have dummy columns or zero/multiple system subjects:
    - validate() only checks table existence and quick_check.
    - independent inspection detects missing columns, missing FKs, and invalid system subject counts.
    """
    target = audit_env.private / "dummy_tables.sqlite3"
    with sqlite3.connect(str(target)) as conn:
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION};")
        # Create all required tables but with degenerate/dummy schema
        for tbl in REQUIRED_TABLES:
            conn.execute(f"CREATE TABLE {tbl} (dummy_col TEXT);")
        conn.commit()

    # validate() passes on dummy tables because table names exist and user_version=2
    validate(target)

    # In contrast, independent read_db_state detects the structural discrepancies
    state = read_db_state(target)
    for tbl in REQUIRED_TABLES:
        col_names = [c["name"] for c in state["tables"][tbl]["columns"]]
        assert "dummy_col" in col_names
        assert "id" not in col_names, f"Dummy table {tbl} should not have canonical 'id' column"

    # Test system subject counts: 0 vs 2 system subjects
    # Canonical DB with 1 system subject
    valid_db = audit_env.init_db(audit_env.private / "one_sys.sqlite3")
    validate(valid_db)
    valid_state = read_db_state(valid_db)
    sys_count_valid = len([s for s in valid_state["tables"]["subjects"]["rows"] if s["is_system"] == 1])
    assert sys_count_valid == 1

    # Modify to 0 system subjects
    no_sys_db = audit_env.private / "zero_sys.sqlite3"
    target.unlink()
    initialize(no_sys_db)
    with sqlite3.connect(str(no_sys_db)) as conn:
        conn.execute("DELETE FROM subjects WHERE is_system = 1;")
        conn.commit()

    # Note: validate() passes because it does not inspect subjects rows
    validate(no_sys_db)
    no_sys_state = read_db_state(no_sys_db)
    sys_count_zero = len([s for s in no_sys_state["tables"]["subjects"]["rows"] if s["is_system"] == 1])
    assert sys_count_zero == 0, "Audit confirmed: independent check detects missing system subject"


@pytest.mark.parametrize("case_id", ["DB-03-04"])
def test_db_03_04_exception_hierarchy(case_id: str) -> None:
    """DB-03-04: DatabaseValidationError inherits from DatabaseError, DatabaseNotFoundError inherits from FileNotFoundError."""
    assert issubclass(DatabaseValidationError, DatabaseError)
    assert issubclass(DatabaseNotFoundError, DatabaseValidationError)
    assert issubclass(DatabaseNotFoundError, FileNotFoundError)


# ===========================================================================
# DB-04: Foreign Key Enforcement & Referential Integrity
# ===========================================================================

@pytest.mark.parametrize("case_id", ["DB-04-01"])
def test_db_04_01_topics_foreign_key(case_id: str, audit_env: AuditEnv) -> None:
    """DB-04-01: topics.subject_id references subjects(id) ON DELETE RESTRICT."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        # Non-existent subject_id rejected
        fake_sub_id = str(canonical_uuid("nonexistent_sub"))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO topics (id, subject_id, name, name_key) VALUES (?, ?, ?, ?);",
                (str(canonical_uuid("t_orphan")), fake_sub_id, "Bad Topic", "bad_topic"),
            )

        # Deleting referenced subject blocked
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute("DELETE FROM subjects WHERE id = ?;", (str(S1_ID),))


@pytest.mark.parametrize("case_id", ["DB-04-02"])
def test_db_04_02_group_access_foreign_key(case_id: str, audit_env: AuditEnv) -> None:
    """DB-04-02: group_access.group_id references groups(id) ON DELETE RESTRICT."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        fake_gid = str(canonical_uuid("nonexistent_group"))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO group_access (group_id, password_hash, is_open, version) VALUES (?, NULL, 0, 1);",
                (fake_gid,),
            )

        # Cannot delete group while group_access exists
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute("DELETE FROM groups WHERE id = ?;", (str(G1_ID),))


@pytest.mark.parametrize("case_id", ["DB-04-03"])
def test_db_04_03_students_foreign_key_and_composite(case_id: str, audit_env: AuditEnv) -> None:
    """DB-04-03: students.group_id references groups(id), UNIQUE(id, group_id)."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        fake_gid = str(canonical_uuid("nonexistent_group"))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, ?);",
                (str(canonical_uuid("st_fake")), fake_gid, "Test Student"),
            )

        # Cannot delete group G1 with active students
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute("DELETE FROM groups WHERE id = ?;", (str(G1_ID),))


@pytest.mark.parametrize("case_id", ["DB-04-04"])
def test_db_04_04_class_assignments_foreign_keys_and_composite(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-04-04: class_assignments references groups and topics; UNIQUE(id, group_id, topic_id)."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        fake_tid = str(canonical_uuid("fake_topic"))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                VALUES (?, ?, ?, '2026-04-01', 3, '2026-03-01T00:00:00Z', '2026-03-01T00:00:00Z');
                """,
                (str(canonical_uuid("bad_ca")), str(G1_ID), fake_tid),
            )


@pytest.mark.parametrize("case_id", ["DB-04-05"])
def test_db_04_05_submissions_composite_fks_and_null_assignment(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-04-05: submissions composite FKs to students and class_assignments, NULL assignment_id allowed."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        # A. Composite mismatch for student: student1 is in G1, attempting submission under G2
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, created_at)
                VALUES (?, ?, ?, ?, ?, 1, 'pending', '2026-03-01T10:00:00Z');
                """,
                (str(canonical_uuid("sub_bad_st_group")), str(STUDENT1_ID), str(G2_ID), str(T1_ID), None),
            )

        # B. Composite mismatch for assignment: A1 is for (G1, T1), attempting submission with T2
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, created_at)
                VALUES (?, ?, ?, ?, ?, 1, 'pending', '2026-03-01T10:00:00Z');
                """,
                (str(canonical_uuid("sub_bad_ca_topic")), str(STUDENT1_ID), str(G1_ID), str(T2_ID), str(A1_ID)),
            )

        # C. Historical NULL assignment_id must be allowed
        valid_sub_id = str(canonical_uuid("sub_historical_null_ca"))
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, created_at)
            VALUES (?, ?, ?, ?, NULL, 1, 'pending', '2026-03-01T10:00:00Z');
            """,
            (valid_sub_id, str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
        )
        row = conn.execute("SELECT assignment_id FROM submissions WHERE id = ?;", (valid_sub_id,)).fetchone()
        assert row["assignment_id"] is None


@pytest.mark.parametrize("case_id", ["DB-04-06"])
def test_db_04_06_submission_files_foreign_key_and_one_to_one(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-04-06: submission_files.submission_id UNIQUE REFERENCES submissions(id) ON DELETE RESTRICT."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        sub_id = str(canonical_uuid("sub_test_files"))
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, created_at)
            VALUES (?, ?, ?, ?, NULL, 1, 'pending', '2026-03-01T10:00:00Z');
            """,
            (sub_id, str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
        )

        f1_id = str(canonical_uuid("f1"))
        conn.execute(
            """
            INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
            VALUES (?, ?, 'main.py', 'path/1/main.py', 10, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
            """,
            (f1_id, sub_id),
        )

        # Enforce 1:1 file per submission (second file for same submission rejected)
        f2_id = str(canonical_uuid("f2"))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: submission_files.submission_id"):
            conn.execute(
                """
                INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
                VALUES (?, ?, 'extra.py', 'path/2/extra.py', 20, NULL);
                """,
                (f2_id, sub_id),
            )

        # Deleting submission blocked while file exists
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute("DELETE FROM submissions WHERE id = ?;", (sub_id,))


# ===========================================================================
# DB-05: Constraints & Domain Checks
# ===========================================================================

@pytest.mark.parametrize("case_id", ["DB-05-01"])
def test_db_05_01_groups_unique_name_key(case_id: str, audit_env: AuditEnv) -> None:
    """DB-05-01: groups table UNIQUE(name_key) rejects duplicate active or archived group names."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        # Same normalized name_key with differing case/whitespace
        duplicate_name = f"  {G1_NAME.lower()}  "
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: groups.name_key"):
            conn.execute(
                "INSERT INTO groups (id, name, name_key) VALUES (?, ?, ?);",
                (str(canonical_uuid("dup_g")), duplicate_name, normalize_name_key(duplicate_name)),
            )


@pytest.mark.parametrize("case_id", ["DB-05-02"])
def test_db_05_02_subjects_constraints_and_system_subject(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-05-02: subjects constraints: one_system_subject, cannot archive system subject, is_system IN (0,1)."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        # Cannot insert second system subject
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: subjects.is_system"):
            conn.execute(
                "INSERT INTO subjects (id, name, name_key, is_system, archived_at) VALUES (?, 'System 2', 'system_2', 1, NULL);",
                (str(canonical_uuid("sys2")),),
            )

        # Cannot archive system subject: CHECK(is_system=0 OR archived_at IS NULL)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "UPDATE subjects SET archived_at = '2026-01-01T00:00:00Z' WHERE is_system = 1;"
            )

        # Invalid is_system flag
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO subjects (id, name, name_key, is_system) VALUES (?, 'Invalid Flag', 'invalid_flag', 2);",
                (str(canonical_uuid("inv_flag")),),
            )


@pytest.mark.parametrize("case_id", ["DB-05-03"])
def test_db_05_03_topics_unique_name_key_and_cross_subject(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-05-03: topics UNIQUE(subject_id, name_key) permits identical topic names under different subjects."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        # T1 (in S1) and T2 (in S2) already have identical name COLLISION_TOPIC_NAME and exist concurrently
        rows = conn.execute(
            "SELECT subject_id, name FROM topics WHERE name = ?;", (COLLISION_TOPIC_NAME,)
        ).fetchall()
        assert len(rows) == 2
        subject_ids = {r["subject_id"] for r in rows}
        assert str(S1_ID) in subject_ids
        assert str(S2_ID) in subject_ids

        # Attempting duplicate topic name in SAME subject S1 must fail
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: topics.subject_id, topics.name_key"):
            conn.execute(
                "INSERT INTO topics (id, subject_id, name, name_key) VALUES (?, ?, ?, ?);",
                (str(canonical_uuid("t1_dup")), str(S1_ID), COLLISION_TOPIC_NAME, normalize_name_key(COLLISION_TOPIC_NAME)),
            )


@pytest.mark.parametrize("case_id", ["DB-05-04"])
def test_db_05_04_class_assignments_active_slot_and_archived(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-05-04: assignment_active_slot partial index prevents active collisions; permits reusing slot when archived."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        # A1 is active at (G1_ID, A1_DATE, A1_PAIR)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: class_assignments.group_id, class_assignments.lesson_date, class_assignments.pair_number"):
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at, archived_at)
                VALUES (?, ?, ?, ?, ?, '2026-03-01T00:00:00Z', '2026-03-01T00:00:00Z', NULL);
                """,
                (str(canonical_uuid("ca_collide")), str(G1_ID), str(T2_ID), A1_DATE, A1_PAIR),
            )

        # Archive A1
        conn.execute(
            "UPDATE class_assignments SET archived_at = '2026-03-01T15:00:00Z' WHERE id = ?;",
            (str(A1_ID),),
        )

        # Now creating new active assignment in the same slot succeeds
        new_ca_id = str(canonical_uuid("ca_after_archive"))
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at, archived_at)
            VALUES (?, ?, ?, ?, ?, '2026-03-01T16:00:00Z', '2026-03-01T16:00:00Z', NULL);
            """,
            (new_ca_id, str(G1_ID), str(T2_ID), A1_DATE, A1_PAIR),
        )
        assert conn.execute("SELECT count(*) FROM class_assignments WHERE id = ?;", (new_ca_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("case_id", ["DB-05-05"])
def test_db_05_05_submissions_attempt_unique_and_checks(
    case_id: str, audit_env: AuditEnv
) -> None:
    """DB-05-05: submissions UNIQUE(student_id, topic_id, attempt_number) and domain CHECK constraints."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        sub_id = str(canonical_uuid("sub1"))
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, reviewed_at, created_at)
            VALUES (?, ?, ?, ?, NULL, 1, 'pending', NULL, '2026-03-01T10:00:00Z');
            """,
            (sub_id, str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
        )

        # Duplicate attempt 1 rejected
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: submissions.student_id, submissions.topic_id, submissions.attempt_number"):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, reviewed_at, created_at)
                VALUES (?, ?, ?, ?, NULL, 1, 'pending', NULL, '2026-03-01T11:00:00Z');
                """,
                (str(canonical_uuid("sub1_dup")), str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
            )

        # Attempt 0 or negative rejected by CHECK(attempt_number >= 1)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, reviewed_at, created_at)
                VALUES (?, ?, ?, ?, NULL, 0, 'pending', NULL, '2026-03-01T11:00:00Z');
                """,
                (str(canonical_uuid("sub_zero_att")), str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
            )

        # review_status 'invalid' rejected
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, reviewed_at, created_at)
                VALUES (?, ?, ?, ?, NULL, 2, 'graded', NULL, '2026-03-01T11:00:00Z');
                """,
                (str(canonical_uuid("sub_bad_status")), str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
            )

        # Pending submission with reviewed_at != NULL rejected: CHECK(review_status='reviewed' OR reviewed_at IS NULL)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                """
                INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, reviewed_at, created_at)
                VALUES (?, ?, ?, ?, NULL, 2, 'pending', '2026-03-01T12:00:00Z', '2026-03-01T11:00:00Z');
                """,
                (str(canonical_uuid("sub_pending_with_ts")), str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
            )

        # Historical exception: reviewed submission with reviewed_at IS NULL is permitted by DDL
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, reviewed_at, created_at)
            VALUES (?, ?, ?, ?, NULL, 2, 'reviewed', NULL, '2026-03-01T11:00:00Z');
            """,
            (str(canonical_uuid("sub_hist_reviewed_null_ts")), str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
        )


@pytest.mark.parametrize("case_id", ["DB-05-06"])
def test_db_05_06_submission_files_constraints(case_id: str, audit_env: AuditEnv) -> None:
    """DB-05-06: submission_files UNIQUE(stored_path), size_bytes >= 0, sha256 format checks."""
    db_path = audit_env.populate_canonical()

    with connect(db_path) as conn:
        sub_id = str(canonical_uuid("sub_for_files"))
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, created_at)
            VALUES (?, ?, ?, ?, NULL, 1, 'pending', '2026-03-01T10:00:00Z');
            """,
            (sub_id, str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
        )

        valid_sha = "a" * 64
        conn.execute(
            """
            INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
            VALUES (?, ?, 'f.zip', 'storage/f.zip', 100, ?);
            """,
            (str(canonical_uuid("sf1")), sub_id, valid_sha),
        )

        # Duplicate stored_path rejected
        sub_id2 = str(canonical_uuid("sub_for_files_2"))
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, created_at)
            VALUES (?, ?, ?, ?, NULL, 2, 'pending', '2026-03-01T11:00:00Z');
            """,
            (sub_id2, str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: submission_files.stored_path"):
            conn.execute(
                """
                INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
                VALUES (?, ?, 'f2.zip', 'storage/f.zip', 100, NULL);
                """,
                (str(canonical_uuid("sf_dup_path")), sub_id2),
            )

        # Negative size rejected by CHECK(size_bytes >= 0)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                """
                INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
                VALUES (?, ?, 'f3.zip', 'storage/neg.zip', -1, NULL);
                """,
                (str(canonical_uuid("sf_neg")), sub_id2),
            )

        # Invalid sha256 length rejected: CHECK(sha256 IS NULL OR length(sha256)=64)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                """
                INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
                VALUES (?, ?, 'f4.zip', 'storage/bad_sha.zip', 50, 'tooshort');
                """,
                (str(canonical_uuid("sf_short_sha")), sub_id2),
            )


# ===========================================================================
# DB-06: Data Normalization, Unicode & Types
# ===========================================================================

@pytest.mark.parametrize("case_id", ["DB-06-01"])
def test_db_06_01_normalize_name_key(case_id: str) -> None:
    """DB-06-01: normalize_name_key() NFC normalization, casefolding, and input validation."""
    # Cyrillic ё vs composed/decomposed
    nfc = unicodedata.normalize("NFC", "Иванов Илья")
    nfd = unicodedata.normalize("NFD", "Иванов Илья")
    assert normalize_name_key(nfc) == normalize_name_key(nfd) == "иванов илья"

    # Whitespace and casefold
    assert normalize_name_key("   ПИ-201 \t\n") == "пи-201"

    # Empty string or only whitespace
    with pytest.raises(ValueError, match="Name cannot be empty"):
        normalize_name_key("")
    with pytest.raises(ValueError, match="Name cannot be empty"):
        normalize_name_key("    \n")

    # Non-string input
    with pytest.raises(TypeError, match="Name must be a string"):
        normalize_name_key(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Name must be a string"):
        normalize_name_key(12345)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("case_id", "date_str", "is_valid", "exc_type", "match_str"),
    [
        ("DB-06-02", "2026-03-01", True, None, ""),
        ("DB-06-02", "2024-02-29", True, None, ""),  # Leap year
        ("DB-06-02", "2025-02-29", False, ValueError, "Invalid calendar date"),  # Non-leap year
        ("DB-06-02", "2026-02-30", False, ValueError, "Invalid calendar date"),  # Impossible date
        ("DB-06-02", "2026-13-01", False, ValueError, "Invalid calendar date"),  # Invalid month
        ("DB-06-02", "01-03-2026", False, ValueError, "Invalid lesson date format"),
        ("DB-06-02", "2026/03/01", False, ValueError, "Invalid lesson date format"),
        ("DB-06-02", 20260301, False, TypeError, "lesson_date must be a string"),
    ],
)
def test_db_06_02_validate_lesson_date(
    case_id: str,
    date_str: Any,
    is_valid: bool,
    exc_type: type[Exception] | None,
    match_str: str,
    audit_env: AuditEnv,
) -> None:
    """DB-06-02: validate_lesson_date() calendar validation vs DDL GLOB behavior."""
    if is_valid:
        assert validate_lesson_date(date_str) == date_str
    else:
        assert exc_type is not None
        with pytest.raises(exc_type, match=match_str):
            validate_lesson_date(date_str)

    # SQLite DDL strictly enforces Gregorian calendar dates via:
    # CHECK(lesson_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND date(lesson_date) IS NOT NULL AND date(lesson_date) = lesson_date)
    # Reject syntactically matching invalid calendar dates like '2026-02-30' in SQLite DDL directly:
    if date_str == "2026-02-30":
        db_path = audit_env.populate_canonical()
        with connect(db_path) as conn:
            ca_id = str(canonical_uuid("ddl_glob_test"))
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                    VALUES (?, ?, ?, '2026-02-30', 4, '2026-03-01T00:00:00Z', '2026-03-01T00:00:00Z');
                    """,
                    (ca_id, str(G1_ID), str(T1_ID)),
                )


@pytest.mark.parametrize(
    ("case_id", "pair_val", "is_valid", "exc_type", "match_str"),
    [
        ("DB-06-03", 1, True, None, ""),
        ("DB-06-03", 6, True, None, ""),
        ("DB-06-03", 0, False, ValueError, "pair_number must be between 1 and 6"),
        ("DB-06-03", 7, False, ValueError, "pair_number must be between 1 and 6"),
        ("DB-06-03", -1, False, ValueError, "pair_number must be between 1 and 6"),
        ("DB-06-03", True, False, TypeError, "pair_number must be an integer"),  # bool rejected
        ("DB-06-03", False, False, TypeError, "pair_number must be an integer"),
        ("DB-06-03", 1.5, False, TypeError, "pair_number must be an integer"),  # float rejected
        ("DB-06-03", "1", False, TypeError, "pair_number must be an integer"),  # str rejected
        ("DB-06-03", None, False, TypeError, "pair_number must be an integer"),
    ],
)
def test_db_06_03_validate_pair_number(
    case_id: str,
    pair_val: Any,
    is_valid: bool,
    exc_type: type[Exception] | None,
    match_str: str,
    audit_env: AuditEnv,
) -> None:
    """DB-06-03: validate_pair_number() strict int check vs DDL BETWEEN behavior."""
    if is_valid:
        assert validate_pair_number(pair_val) == pair_val
    else:
        assert exc_type is not None
        with pytest.raises(exc_type, match=match_str):
            validate_pair_number(pair_val)

    # Document SQLite DDL vs application discrepancy:
    # SQLite DDL uses: CHECK(pair_number BETWEEN 1 AND 6)
    # In SQLite, float 1.5 is numerically BETWEEN 1 and 6, so SQLite allows it unless cast/strict.
    if pair_val == 1.5:
        db_path = audit_env.populate_canonical()
        with connect(db_path) as conn:
            ca_id = str(canonical_uuid("ddl_float_pair_test"))
            conn.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
                VALUES (?, ?, ?, '2026-03-05', 1.5, '2026-03-01T00:00:00Z', '2026-03-01T00:00:00Z');
                """,
                (ca_id, str(G1_ID), str(T1_ID)),
            )
            row = conn.execute("SELECT pair_number FROM class_assignments WHERE id = ?;", (ca_id,)).fetchone()
            assert row["pair_number"] == 1.5
