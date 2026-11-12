"""Audit test suite for homework-submissions SQLite v2 domain contract.

Covers test matrix families DOM-01 through DOM-06:
  - DOM-01: Catalog Domain Lifecycle (Groups, Subjects, Topics)
  - DOM-02: Archival Cascades & Invariants
  - DOM-03: Topic Migration (move_topics)
  - DOM-04: Access Control & Authorization (AccessStore)
  - DOM-05: Class Assignments Store (AssignmentStore)
  - DOM-06: Clean Cutover & Legacy Signatures
"""

from __future__ import annotations

import io
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from werkzeug.security import check_password_hash

from app.assignments import (
    AssignmentConflictError,
    AssignmentInUseError,
    AssignmentNotFoundError,
    AssignmentStore,
    AssignmentValidationError,
)
from app.catalog_store import (
    CatalogConflictError,
    CatalogError,
    CatalogItem,
    CatalogNotFoundError,
    CatalogOperationForbiddenError,
    Topic,
)
from app.database import connect
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_DATE,
    A1_ID,
    A1_PAIR,
    A2_ID,
    G1_ACCESS_VERSION,
    G1_ID,
    G1_NAME,
    G2_ID,
    G2_NAME,
    S1_ID,
    S1_NAME,
    S2_ID,
    S2_NAME,
    STUDENT1_ID,
    SYSTEM_UNASSIGNED_SUBJECT_ID,
    T1_ID,
    T1_NAME,
    T2_ID,
    T2_NAME,
    T_FREE_ID,
)


# ==============================================================================
# DOM-01: Catalog Domain Lifecycle (Groups, Subjects, Topics)
# ==============================================================================

@pytest.mark.parametrize(
    "case_id,entity_kind",
    [
        ("DOM-01-01", "group"),
        ("DOM-01-04", "subject"),
        ("DOM-01-05", "topic"),
    ],
)
def test_dom_01_crud_and_archived_retrieval(
    audit_env: AuditEnv, case_id: str, entity_kind: str
) -> None:
    """DOM-01: create, get, list_active, list_all, rename, and archived ID retrieval."""
    audit_env.init_db()
    store = audit_env.get_catalog_store()

    if entity_kind == "group":
        item = store.create_group("Тестовая Группа 1")
        assert isinstance(item, CatalogItem)
        assert item.name == "Тестовая Группа 1"

        # get active
        fetched = store.get_group(item.id)
        assert fetched is not None
        assert fetched.id == item.id
        assert fetched.name == "Тестовая Группа 1"

        # rename
        renamed = store.rename_group(item.id, "Тестовая Группа Переименованная")
        assert renamed.name == "Тестовая Группа Переименованная"

        # archive
        store.archive_group(item.id)

        # get active excludes archived
        assert store.get_group(item.id, include_archived=False) is None
        # get with include_archived=True retrieves it
        archived_fetched = store.get_group(item.id, include_archived=True)
        assert archived_fetched is not None
        assert archived_fetched.name == "Тестовая Группа Переименованная"

        # list checks
        active_list = store.list_groups(include_archived=False)
        assert not any(g.id == item.id for g in active_list)
        all_list = store.list_groups(include_archived=True)
        assert any(g.id == item.id for g in all_list)

    elif entity_kind == "subject":
        item = store.create_subject("Тестовый Предмет 1")
        assert isinstance(item, CatalogItem)
        assert item.name == "Тестовый Предмет 1"

        fetched = store.get_subject(item.id)
        assert fetched is not None
        assert fetched.id == item.id

        renamed = store.rename_subject(item.id, "Тестовый Предмет Обновлен")
        assert renamed.name == "Тестовый Предмет Обновлен"

        store.archive_subject(item.id)

        assert store.get_subject(item.id, include_archived=False) is None
        archived_fetched = store.get_subject(item.id, include_archived=True)
        assert archived_fetched is not None
        assert archived_fetched.name == "Тестовый Предмет Обновлен"

        active_list = store.list_subjects(include_archived=False)
        assert not any(s.id == item.id for s in active_list)
        all_list = store.list_subjects(include_archived=True)
        assert any(s.id == item.id for s in all_list)

    elif entity_kind == "topic":
        subj = store.create_subject("Предмет Для Тем")
        item = store.create_topic(subj.id, "Тестовая Тема 1")
        assert isinstance(item, Topic)
        assert item.name == "Тестовая Тема 1"
        assert item.subject_id == subj.id

        fetched = store.get_topic(item.id)
        assert fetched is not None
        assert fetched.id == item.id

        renamed = store.rename_topic(item.id, "Тестовая Тема Обновлена")
        assert renamed.name == "Тестовая Тема Обновлена"

        store.archive_topic(item.id)

        assert store.get_topic(item.id, include_archived=False) is None
        archived_fetched = store.get_topic(item.id, include_archived=True)
        assert archived_fetched is not None
        assert archived_fetched.name == "Тестовая Тема Обновлена"

        active_list = store.list_topics(subj.id, include_archived=False)
        assert not any(t.id == item.id for t in active_list)
        all_list = store.list_topics(subj.id, include_archived=True)
        assert any(t.id == item.id for t in all_list)


@pytest.mark.parametrize(
    "case_id,entity_kind",
    [
        ("DOM-01-01", "group"),
        ("DOM-01-04", "subject"),
        ("DOM-01-05", "topic"),
    ],
)
def test_dom_01_absent_id_handling(
    audit_env: AuditEnv, case_id: str, entity_kind: str
) -> None:
    """DOM-01: absent ID lookup returns None; absent ID mutation raises CatalogNotFoundError."""
    audit_env.init_db()
    store = audit_env.get_catalog_store()
    nonexistent = uuid.uuid4()

    if entity_kind == "group":
        assert store.get_group(nonexistent) is None
        assert store.get_group(nonexistent, include_archived=True) is None
        with pytest.raises(CatalogNotFoundError):
            store.rename_group(nonexistent, "Новое Имя")
    elif entity_kind == "subject":
        assert store.get_subject(nonexistent) is None
        assert store.get_subject(nonexistent, include_archived=True) is None
        with pytest.raises(CatalogNotFoundError):
            store.rename_subject(nonexistent, "Новое Имя")
    elif entity_kind == "topic":
        assert store.get_topic(nonexistent) is None
        assert store.get_topic(nonexistent, include_archived=True) is None
        with pytest.raises(CatalogNotFoundError):
            store.rename_topic(nonexistent, "Новое Имя")


def test_dom_01_snapshot_and_contains(audit_env: AuditEnv) -> None:
    """DOM-01-06: snapshot() creates consistent Catalog representation and contains() validates."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()

    catalog = store.snapshot()
    assert catalog.contains(G1_NAME, S1_NAME, T1_NAME)
    assert catalog.contains(G2_NAME, S2_NAME, T2_NAME)
    assert not catalog.contains("Несуществующая Группа", S1_NAME, T1_NAME)
    assert not catalog.contains(G1_NAME, "Несуществующий Предмет", T1_NAME)
    assert not catalog.contains(G1_NAME, S1_NAME, "Несуществующая Тема")


def test_dom_01_group_creates_closed_access(audit_env: AuditEnv) -> None:
    """DOM-01-01: creating group atomically creates closed group_access row (is_open=0, version=1)."""
    audit_env.init_db()
    store = audit_env.get_catalog_store()
    acc_store = audit_env.get_access_store()

    new_grp = store.create_group("АСУ-24")

    # AccessStore.get returns GroupAccess with is_open=False, version=1
    access = acc_store.get(new_grp.id)
    assert access is not None
    assert access.group_id == new_grp.id
    assert access.is_open is False
    assert access.version == 1
    assert access.closes_at is None

    # Verify directly via SQLite connection to ensure database row has password_hash=NULL
    with connect(audit_env.db_path) as conn:
        row = conn.execute(
            "SELECT group_id, password_hash, is_open, closes_at_utc, version FROM group_access WHERE group_id = ?;",
            (str(new_grp.id),),
        ).fetchone()
        assert row is not None
        assert row["password_hash"] is None
        assert row["is_open"] == 0
        assert row["closes_at_utc"] is None
        assert row["version"] == 1


def test_dom_01_group_rename_increments_access_version(audit_env: AuditEnv) -> None:
    """DOM-01-02: renaming group increments group_access version."""
    audit_env.init_db()
    store = audit_env.get_catalog_store()
    acc_store = audit_env.get_access_store()

    grp = store.create_group("АСУ-25")
    initial_access = acc_store.get(grp.id)
    assert initial_access is not None
    initial_version = initial_access.version

    # Rename group
    store.rename_group(grp.id, "АСУ-26")

    # Check that version incremented
    updated_access = acc_store.get(grp.id)
    assert updated_access is not None
    assert updated_access.version == initial_version + 1


@pytest.mark.parametrize(
    "case_id,entity_kind",
    [
        ("DOM-01-01", "group"),
        ("DOM-01-04", "subject"),
        ("DOM-01-05", "topic"),
    ],
)
def test_dom_01_name_conflict_active(
    audit_env: AuditEnv, case_id: str, entity_kind: str
) -> None:
    """DOM-01: Name conflict with active item must raise CatalogConflictError."""
    audit_env.init_db()
    store = audit_env.get_catalog_store()

    if entity_kind == "group":
        store.create_group("ИС-20")
        with pytest.raises(CatalogConflictError):
            # Duplicate name with whitespace/case variation
            store.create_group("  ис-20  ")
    elif entity_kind == "subject":
        store.create_subject("Физика")
        with pytest.raises(CatalogConflictError):
            store.create_subject("  физика  ")
    elif entity_kind == "topic":
        subj1 = store.create_subject("Предмет А")
        store.create_topic(subj1.id, "Механика")
        with pytest.raises(CatalogConflictError):
            store.create_topic(subj1.id, "  механика  ")

        # But identical topic name under a DIFFERENT subject is allowed
        subj2 = store.create_subject("Предмет Б")
        topic2 = store.create_topic(subj2.id, "Механика")
        assert topic2.name == "Механика"


def test_dom_01_name_conflict_with_archived_item(audit_env: AuditEnv) -> None:
    """DOM-01-01: Name conflict with archived item (allowed by audit contract).

    Audit requirement: 'Name conflict with active item (rejected) vs conflict with archived item (allowed).'
    If SQLite v2 schema schema_v2.sql has a table-wide UNIQUE(name_key) constraint rather than
    a partial index WHERE archived_at IS NULL, creating an item with the name of an archived
    item will raise CatalogConflictError instead of being allowed.
    """
    audit_env.init_db()
    store = audit_env.get_catalog_store()

    g1 = store.create_group("Архивируемая Группа")
    store.archive_group(g1.id)

    # Expected contract: recreating with archived name should be allowed.
    # If the schema enforces table-wide UNIQUE, this will raise CatalogConflictError (tracked finding).
    g2 = store.create_group("Архивируемая Группа")
    assert g2.id != g1.id
    assert g2.name == "Архивируемая Группа"


# ==============================================================================
# DOM-02: Archival Cascades & Invariants
# ==============================================================================

def test_dom_02_archive_group_cascade(audit_env: AuditEnv) -> None:
    """DOM-02-01: archive_group cascades to students, assignments, closes access, increments version, and preserves history/files."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()
    acc_store = audit_env.get_access_store()
    asgn_store = audit_env.get_assignment_store()
    sub_store = audit_env.get_submission_store()

    # Create submission and physical file in G1
    sub_file_bytes = b"Sample submission bytes for cascade test"
    sub_record, file_record = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(sub_file_bytes),
        original_filename="lab_report.zip",
        access_version=G1_ACCESS_VERSION,
    )
    initial_version = G1_ACCESS_VERSION

    # Archive group G1
    store.archive_group(G1_ID)

    # 1. Group is archived
    g1_item = store.get_group(G1_ID, include_archived=True)
    assert g1_item is not None
    assert store.get_group(G1_ID, include_archived=False) is None

    # 2. Students in group are archived
    students_active = sub_store.list_students(G1_ID, include_archived=False)
    assert not any(s.id == STUDENT1_ID for s in students_active)
    students_all = sub_store.list_students(G1_ID, include_archived=True)
    assert any(s.id == STUDENT1_ID for s in students_all)

    # 3. Class assignments in group are archived
    assignments_active = asgn_store.list_for_group(G1_ID)
    assert len(assignments_active) == 0
    asgn_archived = asgn_store.get(A1_ID)
    assert asgn_archived is not None
    assert asgn_archived.archived_at is not None

    # 4. Group access is closed and version incremented
    with connect(audit_env.db_path) as conn:
        acc_row = conn.execute(
            "SELECT is_open, closes_at_utc, version FROM group_access WHERE group_id = ?;",
            (str(G1_ID),),
        ).fetchone()
        assert acc_row is not None
        assert acc_row["is_open"] == 0
        assert acc_row["closes_at_utc"] is None
        assert acc_row["version"] == initial_version + 1

    # AccessStore.get on archived group returns None
    assert acc_store.get(G1_ID) is None

    # 5. History and physical submission files preserved
    stored_path = audit_env.storage / file_record.stored_path
    assert stored_path.is_file()
    assert stored_path.read_bytes() == sub_file_bytes

    # Submission metadata in DB is completely intact
    fetched_path, orig_name = sub_store.get_file(sub_record.id)
    assert fetched_path == stored_path
    assert orig_name == "lab_report.zip"
    subs = sub_store.list_submissions(G1_ID, T1_ID)
    assert any(s.id == sub_record.id for s in subs)


def test_dom_02_archive_subject_cascade_and_no_move_to_system(audit_env: AuditEnv) -> None:
    """DOM-02-02: archive_subject cascades to topics and assignments; topics are NEVER moved into system subject U."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()
    asgn_store = audit_env.get_assignment_store()

    # Archive S1
    store.archive_subject(S1_ID)

    # 1. Subject S1 is archived
    assert store.get_subject(S1_ID, include_archived=False) is None
    s1_all = store.get_subject(S1_ID, include_archived=True)
    assert s1_all is not None

    # 2. Child topic T1 is archived
    assert store.get_topic(T1_ID, include_archived=False) is None
    t1_all = store.get_topic(T1_ID, include_archived=True)
    assert t1_all is not None

    # 3. Invariant: topics are NEVER moved into system unassigned subject U during archive
    assert t1_all.subject_id == S1_ID
    assert t1_all.subject_id != SYSTEM_UNASSIGNED_SUBJECT_ID

    with connect(audit_env.db_path) as conn:
        t_row = conn.execute("SELECT subject_id FROM topics WHERE id = ?;", (str(T1_ID),)).fetchone()
        assert t_row is not None
        assert t_row["subject_id"] == str(S1_ID)

    # 4. Assignments of topics under S1 are archived
    asgn = asgn_store.get(A1_ID)
    assert asgn is not None
    assert asgn.archived_at is not None


def test_dom_02_archive_topic_cascade(audit_env: AuditEnv) -> None:
    """DOM-02-03: archive_topic cascades to linked active class assignments."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()
    asgn_store = audit_env.get_assignment_store()

    # Archive topic T1
    store.archive_topic(T1_ID)

    # Topic is archived
    assert store.get_topic(T1_ID, include_archived=False) is None
    assert store.get_topic(T1_ID, include_archived=True) is not None

    # Linked assignments A1 and A2 are archived
    a1 = asgn_store.get(A1_ID)
    assert a1 is not None
    assert a1.archived_at is not None
    a2 = asgn_store.get(A2_ID)
    assert a2 is not None
    assert a2.archived_at is not None


def test_dom_02_system_subject_cannot_be_renamed_or_archived(audit_env: AuditEnv) -> None:
    """DOM-02-02: system subject cannot be renamed or archived (must raise CatalogOperationForbiddenError)."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()

    with connect(audit_env.db_path) as conn:
        sys_row = conn.execute("SELECT id FROM subjects WHERE is_system=1;").fetchone()
        assert sys_row is not None
        system_id = UUID(sys_row["id"])

    # Attempt archive system subject
    with pytest.raises(CatalogOperationForbiddenError):
        store.archive_subject(system_id)

    # Attempt rename system subject
    with pytest.raises(CatalogOperationForbiddenError):
        store.rename_subject(system_id, "Новое Название Системного")


@pytest.mark.parametrize(
    "case_id,entity_kind",
    [
        ("DOM-02-01", "group"),
        ("DOM-02-02", "subject"),
        ("DOM-02-03", "topic"),
    ],
)
def test_dom_02_archive_idempotence(
    audit_env: AuditEnv, case_id: str, entity_kind: str
) -> None:
    """DOM-02: Repeating archive on already-archived item succeeds without error or timestamp alteration."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()

    if entity_kind == "group":
        store.archive_group(G2_ID)
        with connect(audit_env.db_path) as conn:
            ts1 = conn.execute("SELECT archived_at FROM groups WHERE id = ?;", (str(G2_ID),)).fetchone()[0]
        # Repeated call
        res = store.archive_group(G2_ID)
        assert res.id == G2_ID
        with connect(audit_env.db_path) as conn:
            ts2 = conn.execute("SELECT archived_at FROM groups WHERE id = ?;", (str(G2_ID),)).fetchone()[0]
        assert ts1 == ts2

    elif entity_kind == "subject":
        store.archive_subject(S2_ID)
        with connect(audit_env.db_path) as conn:
            ts1 = conn.execute("SELECT archived_at FROM subjects WHERE id = ?;", (str(S2_ID),)).fetchone()[0]
        res = store.archive_subject(S2_ID)
        assert res.id == S2_ID
        with connect(audit_env.db_path) as conn:
            ts2 = conn.execute("SELECT archived_at FROM subjects WHERE id = ?;", (str(S2_ID),)).fetchone()[0]
        assert ts1 == ts2

    elif entity_kind == "topic":
        store.archive_topic(T2_ID)
        with connect(audit_env.db_path) as conn:
            ts1 = conn.execute("SELECT archived_at FROM topics WHERE id = ?;", (str(T2_ID),)).fetchone()[0]
        res = store.archive_topic(T2_ID)
        assert res.id == T2_ID
        with connect(audit_env.db_path) as conn:
            ts2 = conn.execute("SELECT archived_at FROM topics WHERE id = ?;", (str(T2_ID),)).fetchone()[0]
        assert ts1 == ts2


def test_dom_02_archive_nonexistent_ids(audit_env: AuditEnv) -> None:
    """DOM-02: Archiving nonexistent IDs raises appropriate NotFoundError."""
    audit_env.init_db()
    store = audit_env.get_catalog_store()
    asgn_store = audit_env.get_assignment_store()
    nonexistent = uuid.uuid4()

    with pytest.raises(CatalogNotFoundError):
        store.archive_group(nonexistent)

    with pytest.raises(CatalogNotFoundError):
        store.archive_subject(nonexistent)

    with pytest.raises(CatalogNotFoundError):
        store.archive_topic(nonexistent)

    with pytest.raises(AssignmentNotFoundError):
        asgn_store.archive(nonexistent)


def test_dom_02_cascade_failure_rollback(audit_env: AuditEnv) -> None:
    """DOM-02-01: Failure in middle of cascade rolls back completely; no partial archival."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()

    # Create a group with student and assignment
    grp = store.create_group("Группа-Откат")
    subj = store.create_subject("Предмет-Откат")
    top = store.create_topic(subj.id, "Тема-Откат")
    asgn_store = audit_env.get_assignment_store()
    asgn = asgn_store.create(grp.id, top.id, "2026-11-20", 1)

    # Install a temporary trigger on group_access to abort on UPDATE
    with connect(audit_env.db_path) as conn:
        conn.execute(
            f"""
            CREATE TRIGGER abort_group_access_update
            BEFORE UPDATE ON group_access
            WHEN NEW.group_id = '{grp.id}'
            BEGIN
                SELECT RAISE(FAIL, 'Simulated cascade failure on group_access');
            END;
            """
        )

    # Attempt archive_group; should fail and roll back
    with pytest.raises((sqlite3.Error, CatalogError)):
        store.archive_group(grp.id)

    # Verify that group, assignment, and access remain completely unarchived
    with connect(audit_env.db_path) as conn:
        g_row = conn.execute("SELECT archived_at FROM groups WHERE id = ?;", (str(grp.id),)).fetchone()
        assert g_row["archived_at"] is None

        a_row = conn.execute("SELECT archived_at FROM class_assignments WHERE id = ?;", (str(asgn.id),)).fetchone()
        assert a_row["archived_at"] is None

        acc_row = conn.execute("SELECT is_open FROM group_access WHERE group_id = ?;", (str(grp.id),)).fetchone()
        assert acc_row["is_open"] == 0  # was initially 0, not corrupted


# ==============================================================================
# DOM-03: Topic Migration (move_topics)
# ==============================================================================

def test_dom_03_move_topics_single_and_multiple(audit_env: AuditEnv) -> None:
    """DOM-03-01: move_topics reassigns single and multiple topics to target subject atomically."""
    audit_env.init_db()
    store = audit_env.get_catalog_store()

    s1 = store.create_subject("Источник")
    s2 = store.create_subject("Приемник")
    t1 = store.create_topic(s1.id, "Тема А")
    t2 = store.create_topic(s1.id, "Тема Б")

    # Move single topic
    store.move_topics([t1.id], s2.id)
    fetched_t1 = store.get_topic(t1.id)
    assert fetched_t1 is not None
    assert fetched_t1.subject_id == s2.id

    # Move second topic
    t3 = store.create_topic(s1.id, "Тема В")
    store.move_topics([t2.id, t3.id], s2.id)
    assert store.get_topic(t2.id).subject_id == s2.id
    assert store.get_topic(t3.id).subject_id == s2.id


def test_dom_03_move_topics_empty_list(audit_env: AuditEnv) -> None:
    """DOM-03-01: move_topics with empty list returns cleanly without error."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()

    # Empty list must be a no-op
    store.move_topics([], S2_ID)


def test_dom_03_move_topics_invalid_targets(audit_env: AuditEnv) -> None:
    """DOM-03-02: move_topics rejects nonexistent source, nonexistent target, or archived target."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()
    nonexistent = uuid.uuid4()

    # Nonexistent topic
    with pytest.raises(CatalogNotFoundError):
        store.move_topics([nonexistent], S2_ID)

    # Nonexistent target subject
    with pytest.raises(CatalogNotFoundError):
        store.move_topics([T_FREE_ID], nonexistent)

    # Archived target subject
    archived_subj = store.create_subject("Архивный Предмет")
    store.archive_subject(archived_subj.id)
    with pytest.raises(CatalogOperationForbiddenError):
        store.move_topics([T_FREE_ID], archived_subj.id)


def test_dom_03_move_topics_conflict_detection_and_rollback(audit_env: AuditEnv) -> None:
    """DOM-03-02: move_topics detects collisions in target or duplicate names among moving topics; rolls back completely."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()

    # Collision 1: T1 in S1 has same name ("Лабораторная работа 1") as T2 in S2
    with pytest.raises(CatalogConflictError):
        store.move_topics([T1_ID], S2_ID)

    # Verify T1 was not moved
    assert store.get_topic(T1_ID).subject_id == S1_ID

    # Collision 2: moving two topics with identical names into a new subject
    s_new = store.create_subject("Чистый Предмет")
    # T1 and T2 have identical name "Лабораторная работа 1"
    with pytest.raises(CatalogConflictError):
        store.move_topics([T1_ID, T2_ID], s_new.id)

    # Verify neither topic was moved
    assert store.get_topic(T1_ID).subject_id == S1_ID
    assert store.get_topic(T2_ID).subject_id == S2_ID


def test_dom_03_move_topics_within_same_subject(audit_env: AuditEnv) -> None:
    """DOM-03-01: Moving topic within the same subject succeeds without self-collision."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()

    # Moving T1 (which is already in S1) to S1 should succeed
    store.move_topics([T1_ID], S1_ID)
    assert store.get_topic(T1_ID).subject_id == S1_ID


def test_dom_03_move_topics_preserves_submissions_and_history(audit_env: AuditEnv) -> None:
    """DOM-03-01: Topic with submissions: topic ID, submissions, and stored files remain intact; subject resolved via JOIN."""
    audit_env.populate_canonical()
    store = audit_env.get_catalog_store()
    sub_store = audit_env.get_submission_store()
    asgn_store = audit_env.get_assignment_store()

    # Create submission under T1
    sub_bytes = b"Submission under T1 before move"
    sub_rec, _file_rec = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(sub_bytes),
        original_filename="pre_move.zip",
        access_version=G1_ACCESS_VERSION,
    )

    # Create a destination subject with no name collision
    s_dest = store.create_subject("Новый Предмет Без Коллизий")

    # Move T1 to s_dest
    store.move_topics([T1_ID], s_dest.id)

    # 1. Topic ID remains intact, subject_id is s_dest.id
    t1_moved = store.get_topic(T1_ID)
    assert t1_moved is not None
    assert t1_moved.id == T1_ID
    assert t1_moved.subject_id == s_dest.id

    # 2. Assignment A1 still linked to T1; subject_id in assignment query resolves to s_dest
    asgn = asgn_store.get(A1_ID)
    assert asgn is not None
    assert asgn.topic_id == T1_ID
    assert asgn.subject_id == s_dest.id

    # 3. Submission and file remain completely intact
    fetched_path, orig_name = sub_store.get_file(sub_rec.id)
    assert fetched_path.is_file()
    assert fetched_path.read_bytes() == sub_bytes
    assert orig_name == "pre_move.zip"


# ==============================================================================
# DOM-04: Access Control & Authorization (AccessStore)
# ==============================================================================

def test_dom_04_configure_passwords_and_toggles(audit_env: AuditEnv) -> None:
    """DOM-04-01: configure sets password, NULL/empty password, is_open toggling, and increments version."""
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()
    acc_store = audit_env.get_access_store()

    grp = cat_store.create_group("ИБ-21")
    v1 = acc_store.get(grp.id).version

    # Set password and open access
    closes_at = datetime.now(timezone.utc) + timedelta(days=2)
    acc2 = acc_store.configure(grp.id, is_open=True, password="strong-password", closes_at=closes_at)
    assert acc2.is_open is True
    assert acc2.version == v1 + 1

    # Verify password hash is valid
    with connect(audit_env.db_path) as conn:
        h = conn.execute("SELECT password_hash FROM group_access WHERE group_id = ?;", (str(grp.id),)).fetchone()[0]
        assert check_password_hash(h, "strong-password")

    # Toggle is_open to False while retaining password (password=None)
    acc3 = acc_store.configure(grp.id, is_open=False, password=None)
    assert acc3.is_open is False
    assert acc3.version == acc2.version + 1
    with connect(audit_env.db_path) as conn:
        h2 = conn.execute("SELECT password_hash FROM group_access WHERE group_id = ?;", (str(grp.id),)).fetchone()[0]
        assert h2 == h  # unchanged

    # Clear password (password="")
    acc4 = acc_store.configure(grp.id, is_open=True, password="")
    assert acc4.version == acc3.version + 1
    with connect(audit_env.db_path) as conn:
        h3 = conn.execute("SELECT password_hash FROM group_access WHERE group_id = ?;", (str(grp.id),)).fetchone()[0]
        assert h3 is None


def test_dom_04_authorize(audit_env: AuditEnv) -> None:
    """DOM-04-02: authorize validates password, closed status, and archived status."""
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()
    acc_store = audit_env.get_access_store()

    grp = cat_store.create_group("ИБ-22")
    future = datetime.now(timezone.utc) + timedelta(days=5)
    acc_store.configure(grp.id, is_open=True, password="correct-secret", closes_at=future)

    # 1. Correct password
    ok, access = acc_store.authorize(grp.id, "correct-secret")
    assert ok is True
    assert access is not None
    assert access.group_id == grp.id

    # 2. Incorrect password
    ok, access = acc_store.authorize(grp.id, "wrong-password")
    assert ok is False
    assert access is None

    # 3. Empty password against set password
    ok, access = acc_store.authorize(grp.id, "")
    assert ok is False
    assert access is None

    # 4. Closed group
    acc_store.configure(grp.id, is_open=False)
    ok, access = acc_store.authorize(grp.id, "correct-secret")
    assert ok is False
    assert access is None

    # 5. Archived group
    acc_store.configure(grp.id, is_open=True)
    cat_store.archive_group(grp.id)
    ok, access = acc_store.authorize(grp.id, "correct-secret")
    assert ok is False
    assert access is None


def test_dom_04_permits_timezones_and_boundary(audit_env: AuditEnv) -> None:
    """DOM-04-03: permits handles naive, aware, offset datetimes and strictly enforces deadline boundary."""
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()
    acc_store = audit_env.get_access_store()

    grp = cat_store.create_group("ИБ-23")
    deadline = datetime(2026, 11, 20, 15, 0, 0, tzinfo=timezone.utc)
    acc = acc_store.configure(grp.id, is_open=True, password="pass", closes_at=deadline)
    version = acc.version

    # Matching version before deadline
    before_utc = datetime(2026, 11, 20, 14, 59, 59, tzinfo=timezone.utc)
    assert acc_store.permits(grp.id, version, now=before_utc) is True

    # Exactly at deadline boundary (deadline <= now -> closed)
    at_utc = datetime(2026, 11, 20, 15, 0, 0, tzinfo=timezone.utc)
    assert acc_store.permits(grp.id, version, now=at_utc) is False

    # After deadline
    after_utc = datetime(2026, 11, 20, 15, 0, 1, tzinfo=timezone.utc)
    assert acc_store.permits(grp.id, version, now=after_utc) is False

    # Timezone offset aware (+05:00): 20:00:00+05:00 is exactly 15:00:00 UTC
    tz_plus5 = timezone(timedelta(hours=5))
    at_plus5 = datetime(2026, 11, 20, 20, 0, 0, tzinfo=tz_plus5)
    assert acc_store.permits(grp.id, version, now=at_plus5) is False

    before_plus5 = datetime(2026, 11, 20, 19, 59, 59, tzinfo=tz_plus5)
    assert acc_store.permits(grp.id, version, now=before_plus5) is True

    # Stale version check
    assert acc_store.permits(grp.id, version - 1, now=before_utc) is False


def test_dom_04_changing_password_revokes_former(audit_env: AuditEnv) -> None:
    """DOM-04-01: Changing password revokes former password immediately and increments version."""
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()
    acc_store = audit_env.get_access_store()

    grp = cat_store.create_group("ИБ-24")
    acc_store.configure(grp.id, is_open=True, password="old-password")
    ok, _ = acc_store.authorize(grp.id, "old-password")
    assert ok is True

    # Update password
    acc_store.configure(grp.id, is_open=True, password="new-password")

    # Old password immediately rejected
    ok, _ = acc_store.authorize(grp.id, "old-password")
    assert ok is False

    # New password accepted
    ok, _ = acc_store.authorize(grp.id, "new-password")
    assert ok is True


# ==============================================================================
# DOM-05: Class Assignments Store (AssignmentStore)
# ==============================================================================

def test_dom_05_crud_and_queries(audit_env: AuditEnv) -> None:
    """DOM-05-01: create, get, list_all, list_for_group, list_for_group_and_subject."""
    audit_env.populate_canonical()
    asgn_store = audit_env.get_assignment_store()

    # list_for_group on group with canonical assignments
    g1_list = asgn_store.list_for_group(G1_ID)
    assert len(g1_list) >= 2
    assert any(a.id == A1_ID for a in g1_list)

    # list_for_group_and_subject
    g1_s1_list = asgn_store.list_for_group_and_subject(G1_ID, S1_ID)
    assert len(g1_s1_list) >= 2

    # Empty list on group without assignments
    g2_list = asgn_store.list_for_group(G2_ID)
    assert g2_list == []

    # get() returns typed Assignment with subject_id populated
    a1 = asgn_store.get(A1_ID)
    assert a1 is not None
    assert a1.id == A1_ID
    assert a1.group_id == G1_ID
    assert a1.topic_id == T1_ID
    assert a1.subject_id == S1_ID
    assert a1.lesson_date == A1_DATE
    assert a1.pair_number == A1_PAIR


def test_dom_05_invalid_parents(audit_env: AuditEnv) -> None:
    """DOM-05-01: create assignment with nonexistent or archived group/topic raises appropriate error."""
    audit_env.populate_canonical()
    asgn_store = audit_env.get_assignment_store()
    cat_store = audit_env.get_catalog_store()
    nonexistent = uuid.uuid4()

    # Nonexistent group
    with pytest.raises(AssignmentNotFoundError):
        asgn_store.create(nonexistent, T1_ID, "2026-11-25", 1)

    # Nonexistent topic
    with pytest.raises(AssignmentNotFoundError):
        asgn_store.create(G1_ID, nonexistent, "2026-11-25", 1)

    # Archived group
    g_arch = cat_store.create_group("Архивная Для Занятий")
    cat_store.archive_group(g_arch.id)
    with pytest.raises(AssignmentValidationError):
        asgn_store.create(g_arch.id, T1_ID, "2026-11-25", 1)

    # Archived topic
    t_arch = cat_store.create_topic(S1_ID, "Архивная Тема Для Занятий")
    cat_store.archive_topic(t_arch.id)
    with pytest.raises(AssignmentValidationError):
        asgn_store.create(G1_ID, t_arch.id, "2026-11-25", 1)


def test_dom_05_slot_collision_and_reuse_after_archive(audit_env: AuditEnv) -> None:
    """DOM-05-01 & DOM-05-03: Slot collision rejected; slot freed after archiving earlier assignment."""
    audit_env.populate_canonical()
    asgn_store = audit_env.get_assignment_store()

    # A1 occupies slot (G1, A1_DATE, A1_PAIR)
    with pytest.raises(AssignmentConflictError):
        asgn_store.create(G1_ID, T1_ID, A1_DATE, A1_PAIR)

    # Archive A1
    asgn_store.archive(A1_ID)

    # Slot is now free; creating another assignment in same slot must succeed
    new_asgn = asgn_store.create(G1_ID, T1_ID, A1_DATE, A1_PAIR)
    assert new_asgn.id != A1_ID
    assert new_asgn.lesson_date == A1_DATE
    assert new_asgn.pair_number == A1_PAIR


def test_dom_05_topic_change_before_and_after_submissions(audit_env: AuditEnv) -> None:
    """DOM-05-02: Topic change permitted before submissions, but strictly prohibited with AssignmentInUseError once submissions exist."""
    audit_env.populate_canonical()
    asgn_store = audit_env.get_assignment_store()
    cat_store = audit_env.get_catalog_store()
    sub_store = audit_env.get_submission_store()

    # Create fresh assignment A_test under T1
    a_test = asgn_store.create(G1_ID, T1_ID, "2026-11-28", 5)

    # Create an alternative topic T_alt
    t_alt = cat_store.create_topic(S1_ID, "Альтернативная Тема")

    # Before submissions: changing topic succeeds
    updated = asgn_store.update(a_test.id, "2026-11-28", 5, t_alt.id)
    assert updated.topic_id == t_alt.id

    # Create submission for a_test
    sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=a_test.id,
        source=io.BytesIO(b"Work submitted for a_test"),
        original_filename="hw.zip",
        access_version=G1_ACCESS_VERSION,
    )

    # AFTER submissions: attempting to change topic MUST raise AssignmentInUseError
    with pytest.raises(AssignmentInUseError):
        asgn_store.update(a_test.id, "2026-11-28", 5, T1_ID)

    # But updating lesson_date and pair_number WITHOUT changing topic must succeed
    updated_slot = asgn_store.update(a_test.id, "2026-11-29", 6, t_alt.id)
    assert updated_slot.lesson_date == "2026-11-29"
    assert updated_slot.pair_number == 6
    assert updated_slot.topic_id == t_alt.id


def test_dom_05_archived_assignment_history(audit_env: AuditEnv) -> None:
    """DOM-05-04: Archived assignment is hidden from active list but retrievable via get() for history."""
    audit_env.populate_canonical()
    asgn_store = audit_env.get_assignment_store()

    asgn_store.archive(A2_ID)

    # Hidden from group active listing
    g1_active = asgn_store.list_for_group(G1_ID)
    assert not any(a.id == A2_ID for a in g1_active)

    # Retrievable via get()
    a2_fetched = asgn_store.get(A2_ID)
    assert a2_fetched is not None
    assert a2_fetched.id == A2_ID
    assert a2_fetched.archived_at is not None


# ==============================================================================
# DOM-06: Clean Cutover & Legacy Signatures
# ==============================================================================

def test_dom_06_exact_new_signatures_with_keywords(audit_env: AuditEnv) -> None:
    """DOM-06-01: Call exact new public signatures with keyword arguments and UUID objects."""
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()
    asgn_store = audit_env.get_assignment_store()

    # Exact keyword calls
    g = cat_store.create_group(name="Новая Группа КВ")
    assert isinstance(g.id, UUID)

    s = cat_store.create_subject(name="Новый Предмет КВ")
    assert isinstance(s.id, UUID)

    t = cat_store.create_topic(subject_id=s.id, name="Новая Тема КВ")
    assert isinstance(t.id, UUID)

    a = asgn_store.create(
        group_id=g.id,
        topic_id=t.id,
        lesson_date_or_topic="2026-11-30",
        pair_number_or_date=2,
    )
    assert isinstance(a.id, UUID)
    assert a.group_id == g.id
    assert a.topic_id == t.id


def test_dom_06_legacy_delete_alias(audit_env: AuditEnv) -> None:
    """DOM-06-01: Dynamic inspection of legacy alias: AssignmentStore.delete is alias to archive."""
    audit_env.populate_canonical()
    asgn_store = audit_env.get_assignment_store()

    # Inspect alias identity
    assert hasattr(AssignmentStore, "delete")
    assert AssignmentStore.delete == AssignmentStore.archive

    # Verify calling delete() archives assignment
    asgn_store.delete(A1_ID)
    a1 = asgn_store.get(A1_ID)
    assert a1 is not None
    assert a1.archived_at is not None


def test_dom_06_legacy_five_argument_signatures(audit_env: AuditEnv) -> None:
    """DOM-06-02: AssignmentStore.create and update support legacy 5-argument signatures for backwards compatibility."""
    audit_env.populate_canonical()
    asgn_store = audit_env.get_assignment_store()

    # Legacy 5-arg create: (group_id, subject_id, topic_id, lesson_date, pair_number)
    a_legacy = asgn_store.create(G1_ID, S1_ID, T1_ID, "2026-11-23", 4)
    assert a_legacy.group_id == G1_ID
    assert a_legacy.topic_id == T1_ID
    assert a_legacy.lesson_date == "2026-11-23"
    assert a_legacy.pair_number == 4

    # Legacy 5-arg update: (assignment_id, lesson_date, pair_number, subject_id, topic_id)
    a_updated = asgn_store.update(a_legacy.id, "2026-11-24", 5, S1_ID, T1_ID)
    assert a_updated.lesson_date == "2026-11-24"
    assert a_updated.pair_number == 5
    assert a_updated.topic_id == T1_ID


def test_dom_06_standalone_new_contract(audit_env: AuditEnv) -> None:
    """DOM-06-04: Full lifecycle functions strictly using new contract without relying on legacy aliases."""
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()
    asgn_store = audit_env.get_assignment_store()
    acc_store = audit_env.get_access_store()

    g = cat_store.create_group("Чистая Группа")
    s = cat_store.create_subject("Чистый Предмет")
    t = cat_store.create_topic(s.id, "Чистая Тема")

    # configure access
    acc_store.configure(g.id, is_open=True, password="pass")

    # create assignment
    a = asgn_store.create(g.id, t.id, "2026-11-15", 1)

    # update assignment
    a2 = asgn_store.update(a.id, "2026-11-16", 2, t.id)
    assert a2.lesson_date == "2026-11-16"

    # archive assignment via archive()
    asgn_store.archive(a.id)
    assert asgn_store.get(a.id).archived_at is not None
