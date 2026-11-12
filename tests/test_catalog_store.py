"""Tests for SQLite v2 catalog store and factory."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.access import AccessStore
from app.assignments import AssignmentStore
from app.catalog_store import (
    CatalogConflictError,
    CatalogItem,
    CatalogNotFoundError,
    CatalogOperationForbiddenError,
    CatalogValidationError,
    Topic,
    create_catalog_store,
)
from app.database import connect, initialize
from app.sqlite_catalog import SqliteCatalogStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "app_v2.sqlite3"
    initialize(path)
    return path


@pytest.fixture
def store(db_path: Path) -> SqliteCatalogStore:
    return SqliteCatalogStore(db_path)


# --- 1. Factory Function ---

def test_create_catalog_store_factory(tmp_path: Path, db_path: Path):
    # Relative path with PRIVATE_DATA_DIR
    configured_store = create_catalog_store({
        "DATABASE_PATH": "app_v2.sqlite3",
        "PRIVATE_DATA_DIR": str(tmp_path),
    })
    assert isinstance(configured_store, SqliteCatalogStore)

    # Absolute path
    configured_store_abs = create_catalog_store({
        "DATABASE_PATH": str(db_path),
    })
    assert isinstance(configured_store_abs, SqliteCatalogStore)

    # Missing DATABASE_PATH and unknown provider
    with pytest.raises(ValueError, match="DATABASE_PATH"):
        create_catalog_store({"CATALOG_PROVIDER": "unknown"})


# --- 2. Snapshot and Initialization ---

def test_snapshot_includes_initial_system_subject_and_active_items(store: SqliteCatalogStore):
    snapshot = store.snapshot()
    assert len(snapshot.groups) == 0
    assert len(snapshot.subjects) == 1
    assert snapshot.subjects[0].name == "Без предмета"
    assert snapshot.unassigned_subject_id == snapshot.subjects[0].id
    assert len(snapshot.topics) == 0

    group = store.create_group("Группа-1")
    subject = store.create_subject("Физика")
    topic = store.create_topic(subject.id, "Оптика")

    snapshot2 = store.snapshot()
    assert len(snapshot2.groups) == 1
    assert snapshot2.groups[0] == group
    assert any(s.id == subject.id for s in snapshot2.subjects)
    assert any(t.id == topic.id for t in snapshot2.topics)
    assert snapshot2.contains("Группа-1", "Физика", "Оптика") is True
    assert snapshot2.contains("Группа-1", "Физика", "Несуществующая тема") is False


# --- 3. Groups CRUD and Archiving ---

def test_group_lifecycle(store: SqliteCatalogStore, db_path: Path):
    # Create group
    group = store.create_group("  ИС 25  ")
    assert isinstance(group, CatalogItem)
    assert group.name == "ИС 25"

    # Name conflict (casefold / whitespace)
    with pytest.raises(CatalogConflictError):
        store.create_group("ис 25")

    # Invalid names
    with pytest.raises((ValueError, CatalogValidationError)):
        store.create_group("   ")

    # Get group
    fetched = store.get_group(group.id)
    assert fetched == group

    # Rename group
    renamed = store.rename_group(group.id, "ИС 26")
    assert renamed.name == "ИС 26"

    # Verify group_access version incremented on rename
    access_store = AccessStore(db_path)
    access = access_store.get(group.id)
    assert access is not None
    assert access.version >= 2

    # Archive group
    archived = store.archive_group(group.id)
    assert archived.id == group.id

    # Active list excludes archived
    assert len(store.list_groups(include_archived=False)) == 0
    assert len(store.list_groups(include_archived=True)) == 1

    # Cannot rename archived group
    with pytest.raises(CatalogOperationForbiddenError):
        store.rename_group(group.id, "ИС 27")


def test_group_archive_cascades_to_assignments_and_students(
    store: SqliteCatalogStore, db_path: Path
):
    group = store.create_group("Группа-Каскад")
    subject = store.create_subject("Информатика")
    topic = store.create_topic(subject.id, "Базы данных")

    asgn_store = AssignmentStore(db_path)
    asgn = asgn_store.create(group.id, topic.id, "2026-10-14", 1)

    # Insert student
    student_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, 'Студент');",
            (student_id, str(group.id)),
        )

    # Archive group
    store.archive_group(group.id)

    # Assignment should be archived
    asgn_fetched = asgn_store.get(asgn.id)
    assert asgn_fetched is not None
    assert asgn_fetched.archived_at is not None

    # Student should be archived
    with connect(db_path) as conn:
        st_row = conn.execute(
            "SELECT archived_at FROM students WHERE id = ?;", (student_id,)
        ).fetchone()
        assert st_row["archived_at"] is not None

    # Access is closed
    access_store = AccessStore(db_path)
    assert access_store.get(group.id) is None


# --- 4. Subjects CRUD and Archiving ---

def test_subject_lifecycle(store: SqliteCatalogStore):
    subject = store.create_subject("  Математика  ")
    assert subject.name == "Математика"

    # Name conflict
    with pytest.raises(CatalogConflictError):
        store.create_subject("математика")

    # Rename
    renamed = store.rename_subject(subject.id, "Высшая математика")
    assert renamed.name == "Высшая математика"

    # Archive
    store.archive_subject(subject.id)
    assert len([s for s in store.list_subjects() if s.id == subject.id]) == 0
    assert any(s.id == subject.id for s in store.list_subjects(include_archived=True))


def test_system_subject_cannot_be_renamed_or_archived(store: SqliteCatalogStore):
    sys_subject = next(s for s in store.list_subjects() if s.name == "Без предмета")

    with pytest.raises(CatalogOperationForbiddenError, match="Cannot rename system subject"):
        store.rename_subject(sys_subject.id, "Новое имя")

    with pytest.raises(CatalogOperationForbiddenError, match="Cannot archive system subject"):
        store.archive_subject(sys_subject.id)


def test_subject_archive_cascades_to_topics_and_assignments(
    store: SqliteCatalogStore, db_path: Path
):
    group = store.create_group("Группа-Тест")
    subject = store.create_subject("Алгебра")
    topic = store.create_topic(subject.id, "Линейные уравнения")

    asgn_store = AssignmentStore(db_path)
    asgn = asgn_store.create(group.id, topic.id, "2026-10-14", 1)

    store.archive_subject(subject.id)

    # Topic is archived
    topic_fetched = store.get_topic(topic.id, include_archived=True)
    assert topic_fetched is not None
    assert store.get_topic(topic.id, include_archived=False) is None

    # Assignment is archived
    asgn_fetched = asgn_store.get(asgn.id)
    assert asgn_fetched is not None
    assert asgn_fetched.archived_at is not None


# --- 5. Topics CRUD and Archiving ---

def test_topic_lifecycle(store: SqliteCatalogStore):
    subject = store.create_subject("Химия")
    topic = store.create_topic(subject.id, "Органическая химия")
    assert isinstance(topic, Topic)
    assert topic.name == "Органическая химия"
    assert topic.subject_id == subject.id

    # Duplicate topic in same subject rejected
    with pytest.raises(CatalogConflictError):
        store.create_topic(subject.id, "органическая химия")

    # Same topic name in different subject allowed
    other_subj = store.create_subject("Биохимия")
    other_topic = store.create_topic(other_subj.id, "Органическая химия")
    assert other_topic.id != topic.id

    # Rename topic
    renamed = store.rename_topic(topic.id, "Неорганическая химия")
    assert renamed.name == "Неорганическая химия"

    # Archive topic
    store.archive_topic(topic.id)
    assert store.get_topic(topic.id, include_archived=False) is None
    assert store.get_topic(topic.id, include_archived=True) is not None


def test_archive_topic_cascades_to_assignments(store: SqliteCatalogStore, db_path: Path):
    group = store.create_group("Группа-Темы")
    subject = store.create_subject("Биология")
    topic = store.create_topic(subject.id, "Клетка")

    asgn_store = AssignmentStore(db_path)
    asgn = asgn_store.create(group.id, topic.id, "2026-10-14", 1)

    store.archive_topic(topic.id)

    asgn_fetched = asgn_store.get(asgn.id)
    assert asgn_fetched is not None
    assert asgn_fetched.archived_at is not None


# --- 6. Move Topics Atomicity and Conflict Handling ---

def test_move_topics_success_and_conflict(store: SqliteCatalogStore):
    sub1 = store.create_subject("Предмет 1")
    sub2 = store.create_subject("Предмет 2")

    t1 = store.create_topic(sub1.id, "Тема 1")
    t2 = store.create_topic(sub1.id, "Тема 2")

    # Successful move
    store.move_topics([t1.id, t2.id], sub2.id)

    # Topics now belong to sub2
    t1_moved = store.get_topic(t1.id)
    t2_moved = store.get_topic(t2.id)
    assert t1_moved is not None and t1_moved.subject_id == sub2.id
    assert t2_moved is not None and t2_moved.subject_id == sub2.id

    # Conflict in target subject
    sub3 = store.create_subject("Предмет 3")
    t3 = store.create_topic(sub3.id, "Тема 1")  # Same name as t1

    with pytest.raises(CatalogConflictError):
        store.move_topics([t3.id], sub2.id)

    # Verify t3 was not moved (atomic)
    t3_unmoved = store.get_topic(t3.id)
    assert t3_unmoved is not None and t3_unmoved.subject_id == sub3.id


def test_move_topics_rejects_missing_or_archived(store: SqliteCatalogStore):
    sub = store.create_subject("Активный предмет")
    archived_sub = store.create_subject("Архивный предмет")
    t = store.create_topic(sub.id, "Тема")
    store.archive_subject(archived_sub.id)

    # Target is archived
    with pytest.raises(CatalogOperationForbiddenError):
        store.move_topics([t.id], archived_sub.id)

    # Non-existent topic
    with pytest.raises(CatalogNotFoundError):
        store.move_topics([uuid.uuid4()], sub.id)

    # Non-existent target
    with pytest.raises(CatalogNotFoundError):
        store.move_topics([t.id], uuid.uuid4())
