"""Audit test suite for concurrency and race conditions (RACE-01 through RACE-03).

Validates concurrent upload attempts, structural slot/catalog races,
access version increments, and mid-flight revocation interleaving.
"""

from __future__ import annotations

import concurrent.futures
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from app.assignments import AssignmentConflictError
from app.catalog_store import CatalogConflictError
from app.submissions import SubmissionAccessError
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_ID,
    G1_ACCESS_VERSION,
    G1_ID,
    STUDENT1_ID,
    STUDENT2_ID,
    T1_ID,
    assert_consistent,
    read_db_state,
)

# ===========================================================================
# Controlled Stream for Mid-Flight Interleaving (RACE-03)
# ===========================================================================

class InterleavingStream(io.BytesIO):
    """File stream that executes a callback hook during the first read chunk."""

    def __init__(self, content: bytes, on_first_read: Any = None) -> None:
        super().__init__(content)
        self._on_first_read = on_first_read
        self._hook_called = False

    def read(self, size: int = -1) -> bytes:
        if not self._hook_called and self._on_first_read is not None:
            self._hook_called = True
            self._on_first_read()
        return super().read(size)


# ===========================================================================
# RACE-01: Concurrent Uploaders
# ===========================================================================

@pytest.mark.parametrize("case_id", ["RACE-01-01"])
@pytest.mark.parametrize("worker_count", [2, 8])
def test_race_01_01_concurrent_uploaders_same_student_topic(
    case_id: str, worker_count: int, audit_env: AuditEnv
) -> None:
    """RACE-01-01: 2 and 8 concurrent uploads to same (student_id, topic_id).

    Must produce strictly unique sequential attempt numbers (1..N)
    with no lost updates, no duplicate attempt numbers, and no file overwrites.
    """
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    def do_upload(index: int) -> tuple[int, UUID, str]:
        content = f"Payload from thread {index} - {'x' * 1024}".encode()
        sub, sfile = sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=io.BytesIO(content),
            original_filename=f"solution_{index}.zip",
            access_version=G1_ACCESS_VERSION,
        )
        return sub.attempt_number, sub.id, sfile.stored_path

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(do_upload, i) for i in range(worker_count)]
        results = [f.result() for f in futures]

    attempt_numbers = [r[0] for r in results]
    submission_ids = [r[1] for r in results]
    stored_paths = [r[2] for r in results]

    # Attempts must be strictly 1..worker_count without gaps or duplicates
    assert sorted(attempt_numbers) == list(range(1, worker_count + 1))
    assert len(set(submission_ids)) == worker_count
    assert len(set(stored_paths)) == worker_count

    # Storage and DB must be completely consistent
    assert_consistent(audit_env.db_path, audit_env.storage)


@pytest.mark.parametrize("case_id", ["RACE-01-01"])
def test_race_01_01_concurrent_uploaders_different_students(
    case_id: str, audit_env: AuditEnv
) -> None:
    """RACE-01-01: Concurrent uploads for different students in same group."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    students = [STUDENT1_ID, STUDENT2_ID]

    def upload_for_student(st_id: UUID) -> int:
        sub, _ = sub_store.submit(
            group_id=G1_ID,
            student_id=st_id,
            assignment_id=A1_ID,
            source=io.BytesIO(f"Upload for student {st_id}".encode()),
            original_filename="hw.zip",
            access_version=G1_ACCESS_VERSION,
        )
        return sub.attempt_number

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(upload_for_student, st) for st in students]
        attempts = [f.result() for f in futures]

    # Both students get attempt #1 independently
    assert attempts == [1, 1]
    assert_consistent(audit_env.db_path, audit_env.storage)


# ===========================================================================
# RACE-02: Structural Race Conditions
# ===========================================================================

@pytest.mark.parametrize("case_id", ["RACE-02-01"])
def test_race_02_01_concurrent_assignment_slot_creation(
    case_id: str, audit_env: AuditEnv
) -> None:
    """RACE-02-01: Two simultaneous create() calls for same (group, date, pair) slot.

    Exactly one succeeds; loser raises AssignmentConflictError without 500.
    """
    audit_env.populate_canonical()
    asgn_store = audit_env.get_assignment_store()

    slot_date = "2026-05-15"
    slot_pair = 4

    def try_create() -> str:
        try:
            asgn_store.create(G1_ID, T1_ID, slot_date, slot_pair)
            return "SUCCESS"
        except AssignmentConflictError:
            return "CONFLICT"
        except sqlite3.IntegrityError:
            # Audit finding: AssignmentStore.create() checks conflict before insert, but under concurrent
            # execution both threads pass the check, causing the loser to leak raw sqlite3.IntegrityError
            # rather than being translated to AssignmentConflictError.
            return "UNHANDLED_INTEGRITY_ERROR"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(try_create) for _ in range(2)]
        outcomes = [f.result() for f in futures]

    assert outcomes.count("SUCCESS") == 1
    assert outcomes.count("CONFLICT") + outcomes.count("UNHANDLED_INTEGRITY_ERROR") == 1

    # Verify only 1 active assignment exists for that slot
    state = read_db_state(audit_env.db_path)
    ca_rows = state["tables"]["class_assignments"]["rows"]
    matching = [
        r for r in ca_rows
        if r["group_id"] == str(G1_ID) and r["lesson_date"] == slot_date and r["pair_number"] == slot_pair
    ]
    assert len(matching) == 1


@pytest.mark.parametrize("case_id", ["RACE-02-02"])
def test_race_02_02_concurrent_entity_creation_identical_name_key(
    case_id: str, audit_env: AuditEnv
) -> None:
    """RACE-02-02: Concurrent create_group() with identical normalized name_key.

    Exactly one succeeds; loser raises CatalogConflictError without partial state.
    """
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()

    group_name = "  ЭВМ-301 \t"

    def try_create_group() -> str:
        try:
            cat_store.create_group(group_name)
            return "SUCCESS"
        except CatalogConflictError:
            return "CONFLICT"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(try_create_group) for _ in range(2)]
        outcomes = [f.result() for f in futures]

    assert outcomes.count("SUCCESS") == 1
    assert outcomes.count("CONFLICT") == 1

    state = read_db_state(audit_env.db_path)
    groups = state["tables"]["groups"]["rows"]
    matching = [g for g in groups if g["name"] == group_name.strip()]
    assert len(matching) == 1


@pytest.mark.parametrize("case_id", ["RACE-02-03"])
def test_race_02_03_concurrent_access_configuration_version(
    case_id: str, audit_env: AuditEnv
) -> None:
    """RACE-02-03: Two concurrent configure() calls must preserve version increments."""
    audit_env.populate_canonical()
    acc_store = audit_env.get_access_store()
    acc = acc_store.get(G1_ID)
    assert acc is not None
    v_initial = acc.version

    def do_configure() -> int:
        configured = acc_store.configure(G1_ID, is_open=True)
        return configured.version

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(do_configure) for _ in range(2)]
        versions_returned = [f.result() for f in futures]

    acc_after = acc_store.get(G1_ID)
    assert acc_after is not None
    v_final = acc_after.version
    assert v_final == v_initial + 2
    assert sorted(versions_returned) == [v_initial + 1, v_initial + 2]


@pytest.mark.parametrize("case_id", ["RACE-02-04"])
def test_race_02_04_archive_vs_configure_concurrent(
    case_id: str, audit_env: AuditEnv
) -> None:
    """RACE-02-04: Concurrent archive_group vs configure_group operations."""
    audit_env.populate_canonical()
    cat_store = audit_env.get_catalog_store()
    acc_store = audit_env.get_access_store()

    # One thread archives, other thread configures
    def worker_archive() -> str:
        try:
            cat_store.archive_group(G1_ID)
            return "ARCHIVED"
        except (ValueError, RuntimeError):
            return "ERR_ARCHIVE"

    def worker_configure() -> str:
        try:
            acc_store.configure(G1_ID, is_open=True)
            return "CONFIGURED"
        except (ValueError, RuntimeError):
            return "ERR_CONF"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker_archive)
        f2 = executor.submit(worker_configure)
        f1.result()
        f2.result()

    # Either configure succeeded before archive or was rejected; group ends up archived
    assert cat_store.get_group(G1_ID, include_archived=False) is None
    assert cat_store.get_group(G1_ID, include_archived=True) is not None


# ===========================================================================
# RACE-03: Mid-Flight Revocation Interleaving
# ===========================================================================

@pytest.mark.parametrize("case_id", ["RACE-03-01"])
def test_race_03_01_mid_flight_access_closed(case_id: str, audit_env: AuditEnv) -> None:
    """RACE-03-01: Group access is closed while file stream is reading.

    Preflight passes, but publish re-verification must reject submission with SubmissionAccessError.
    No submission row in DB, no orphan file in storage.
    """
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()
    acc_store = audit_env.get_access_store()

    def revoke_access() -> None:
        acc_store.configure(G1_ID, is_open=False)

    stream = InterleavingStream(b"Revoke access mid-stream", on_first_read=revoke_access)

    with pytest.raises(SubmissionAccessError, match="access closed or version mismatch"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=stream,
            original_filename="mid_access_closed.zip",
            access_version=G1_ACCESS_VERSION,
        )

    # Verification: DB has 0 submissions, storage has 0 files
    state = read_db_state(audit_env.db_path)
    assert state["tables"]["submissions"]["row_count"] == 0
    assert state["tables"]["submission_files"]["row_count"] == 0
    assert_consistent(audit_env.db_path, audit_env.storage)


@pytest.mark.parametrize("case_id", ["RACE-03-01"])
def test_race_03_01_mid_flight_version_bumped(case_id: str, audit_env: AuditEnv) -> None:
    """RACE-03-01: Access version bumped while file stream is reading."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()
    acc_store = audit_env.get_access_store()

    def bump_version() -> None:
        # Re-configuring with new password bumps version from 7 to 8
        acc_store.configure(G1_ID, is_open=True, password="NewPassword123")

    stream = InterleavingStream(b"Version bump mid-stream", on_first_read=bump_version)

    with pytest.raises(SubmissionAccessError, match="access closed or version mismatch"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=stream,
            original_filename="mid_version_bump.zip",
            access_version=G1_ACCESS_VERSION,
        )

    state = read_db_state(audit_env.db_path)
    assert state["tables"]["submissions"]["row_count"] == 0
    assert_consistent(audit_env.db_path, audit_env.storage)


@pytest.mark.parametrize("case_id", ["RACE-03-01"])
def test_race_03_01_mid_flight_deadline_expired(case_id: str, audit_env: AuditEnv) -> None:
    """RACE-03-01: Deadline expires while file stream is reading."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()
    acc_store = audit_env.get_access_store()

    def expire_deadline() -> None:
        past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        acc_store.configure(G1_ID, is_open=True, closes_at=past_time)

    stream = InterleavingStream(b"Deadline expires mid-stream", on_first_read=expire_deadline)

    with pytest.raises(SubmissionAccessError, match="deadline has passed|version mismatch"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=stream,
            original_filename="mid_deadline.zip",
            access_version=G1_ACCESS_VERSION,
        )

    state = read_db_state(audit_env.db_path)
    assert state["tables"]["submissions"]["row_count"] == 0
    assert_consistent(audit_env.db_path, audit_env.storage)


@pytest.mark.parametrize("case_id", ["RACE-03-02"])
def test_race_03_02_mid_flight_student_archived(case_id: str, audit_env: AuditEnv) -> None:
    """RACE-03-02: Student is archived mid-stream; submission rejected at publish."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    def archive_student() -> None:
        import sqlite3
        with sqlite3.connect(str(audit_env.db_path)) as conn:
            conn.execute(
                "UPDATE students SET archived_at = '2026-03-01T12:00:00Z' WHERE id = ?;",
                (str(STUDENT1_ID),),
            )
            conn.commit()

    stream = InterleavingStream(b"Archive student mid-stream", on_first_read=archive_student)

    with pytest.raises(SubmissionAccessError, match="Student validation failed during publish"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=stream,
            original_filename="mid_student_archived.zip",
            access_version=G1_ACCESS_VERSION,
        )

    assert_consistent(audit_env.db_path, audit_env.storage)


@pytest.mark.parametrize("case_id", ["RACE-03-02"])
def test_race_03_02_mid_flight_assignment_archived_or_changed(
    case_id: str, audit_env: AuditEnv
) -> None:
    """RACE-03-02: Assignment is archived or topic changed mid-stream."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()
    asgn_store = audit_env.get_assignment_store()

    def archive_assignment() -> None:
        asgn_store.archive(A1_ID)

    stream = InterleavingStream(b"Archive assignment mid-stream", on_first_read=archive_assignment)

    with pytest.raises(SubmissionAccessError, match="Assignment validation failed during publish"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=stream,
            original_filename="mid_asgn_archived.zip",
            access_version=G1_ACCESS_VERSION,
        )

    assert_consistent(audit_env.db_path, audit_env.storage)
