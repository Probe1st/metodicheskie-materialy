"""Tests for SQLite v2 class assignments management."""

from __future__ import annotations

import uuid
from pathlib import Path
from uuid import UUID

import pytest

from app.assignments import (
    Assignment,
    AssignmentConflictError,
    AssignmentInUseError,
    AssignmentNotFoundError,
    AssignmentStore,
    AssignmentValidationError,
)
from app.database import connect, initialize
from app.sqlite_catalog import SqliteCatalogStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "app_v2.sqlite3"
    initialize(path)
    return path


@pytest.fixture
def catalog(db_path: Path) -> SqliteCatalogStore:
    return SqliteCatalogStore(db_path)


@pytest.fixture
def store(db_path: Path) -> AssignmentStore:
    return AssignmentStore(db_path)


@pytest.fixture
def seeded_ids(catalog: SqliteCatalogStore) -> tuple[UUID, UUID, UUID]:
    group = catalog.create_group("ИС 25/9-1П")
    subject = catalog.create_subject("Информатика")
    topic = catalog.create_topic(subject.id, "Введение в базы данных")
    return group.id, subject.id, topic.id


def test_create_and_get_assignment(
    store: AssignmentStore, seeded_ids: tuple[UUID, UUID, UUID]
):
    group_id, subject_id, topic_id = seeded_ids

    assignment = store.create(
        group_id=group_id,
        topic_id=topic_id,
        lesson_date_or_topic="2026-10-14",
        pair_number_or_date=2,
    )

    assert isinstance(assignment, Assignment)
    assert assignment.group_id == group_id
    assert assignment.topic_id == topic_id
    assert assignment.subject_id == subject_id
    assert assignment.lesson_date == "2026-10-14"
    assert assignment.pair_number == 2
    assert assignment.archived_at is None

    fetched = store.get(assignment.id)
    assert fetched == assignment


def test_unique_group_date_pair_slot_conflict(
    catalog: SqliteCatalogStore,
    store: AssignmentStore,
    seeded_ids: tuple[UUID, UUID, UUID],
):
    group_id, subject_id, topic_id_1 = seeded_ids
    topic_2 = catalog.create_topic(subject_id, "Архитектура ЭВМ")

    store.create(group_id, topic_id_1, "2026-10-14", 2)

    with pytest.raises(AssignmentConflictError):
        store.create(group_id, topic_2.id, "2026-10-14", 2)


def test_pair_number_and_date_validation(
    store: AssignmentStore, seeded_ids: tuple[UUID, UUID, UUID]
):
    group_id, _, topic_id = seeded_ids

    # Invalid pair numbers
    with pytest.raises(AssignmentValidationError):
        store.create(group_id, topic_id, "2026-10-14", 0)

    with pytest.raises(AssignmentValidationError):
        store.create(group_id, topic_id, "2026-10-14", 7)

    with pytest.raises(AssignmentValidationError):
        store.create(group_id, topic_id, "2026-10-14", True)  # type: ignore[arg-type]

    # Invalid dates
    with pytest.raises(AssignmentValidationError):
        store.create(group_id, topic_id, "not-a-date", 1)

    with pytest.raises(AssignmentValidationError):
        store.create(group_id, topic_id, "2026-02-29", 1)


def test_update_assignment_and_conflict_checks(
    catalog: SqliteCatalogStore,
    store: AssignmentStore,
    seeded_ids: tuple[UUID, UUID, UUID],
):
    group_id, subject_id, topic_1 = seeded_ids
    topic_2 = catalog.create_topic(subject_id, "Сетевые технологии")

    asgn1 = store.create(group_id, topic_1, "2026-10-14", 1)
    _asgn2 = store.create(group_id, topic_1, "2026-10-14", 2)

    # Update asgn1 to topic 2, same slot
    updated = store.update(
        assignment_id=asgn1.id,
        lesson_date="2026-10-14",
        pair_number=1,
        topic_id_or_subject=topic_2.id,
    )
    assert updated.topic_id == topic_2.id
    assert updated.pair_number == 1

    # Conflict: updating asgn1 to slot of asgn2
    with pytest.raises(AssignmentConflictError):
        store.update(asgn1.id, "2026-10-14", 2, topic_2.id)


def test_update_assignment_in_use_rejected(
    db_path: Path,
    catalog: SqliteCatalogStore,
    store: AssignmentStore,
    seeded_ids: tuple[UUID, UUID, UUID],
):
    group_id, subject_id, topic_1 = seeded_ids
    topic_2 = catalog.create_topic(subject_id, "Операционные системы")

    asgn = store.create(group_id, topic_1, "2026-10-14", 3)

    # Create student and submission referencing this assignment
    student_id = str(uuid.uuid4())
    subm_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, 'Студент');",
            (student_id, str(group_id)),
        )
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, created_at)
            VALUES (?, ?, ?, ?, ?, 1, 'now');
            """,
            (subm_id, student_id, str(group_id), str(topic_1), str(asgn.id)),
        )

    # Attempting to change topic_id when submissions exist must raise AssignmentInUseError
    with pytest.raises(AssignmentInUseError, match="Cannot change topic"):
        store.update(asgn.id, "2026-10-14", 3, topic_2.id)

    # Updating date/pair without changing topic_id succeeds
    updated = store.update(asgn.id, "2026-10-15", 4, topic_1)
    assert updated.lesson_date == "2026-10-15"
    assert updated.pair_number == 4


def test_archive_frees_slot_and_preserves_retrieval(
    store: AssignmentStore, seeded_ids: tuple[UUID, UUID, UUID]
):
    group_id, _, topic_id = seeded_ids

    asgn = store.create(group_id, topic_id, "2026-10-14", 1)
    store.archive(asgn.id)

    # Get still retrieves archived assignment
    archived = store.get(asgn.id)
    assert archived is not None
    assert archived.archived_at is not None

    # Slot can now be reused by a new assignment
    new_asgn = store.create(group_id, topic_id, "2026-10-14", 1)
    assert new_asgn.id != asgn.id
    assert len(store.list_for_group(group_id)) == 1

    with pytest.raises(AssignmentNotFoundError):
        store.archive(uuid.uuid4())

    assert store.get(uuid.uuid4()) is None

def test_list_filtering_and_ordering(
    catalog: SqliteCatalogStore,
    store: AssignmentStore,
    seeded_ids: tuple[UUID, UUID, UUID],
):
    group_1, subject_1, topic_1 = seeded_ids
    group_2 = catalog.create_group("ИС 25/9-2П")
    subject_2 = catalog.create_subject("Физика")
    topic_2 = catalog.create_topic(subject_2.id, "Механика")
    # Create interleaved assignments
    store.create(group_1, topic_1, "2026-10-15", 2)
    store.create(group_1, topic_1, "2026-10-14", 3)
    store.create(group_1, topic_2.id, "2026-10-14", 1)
    store.create(group_2.id, topic_1, "2026-10-14", 1)

    # List for group_1 (sorted by date, pair)
    g1_list = store.list_for_group(group_1)
    assert len(g1_list) == 3
    assert [a.lesson_date for a in g1_list] == ["2026-10-14", "2026-10-14", "2026-10-15"]
    assert [a.pair_number for a in g1_list] == [1, 3, 2]

    # List for group_1 and subject_1
    g1_s1_list = store.list_for_group_and_subject(group_1, subject_1)
    assert len(g1_s1_list) == 2
    assert all(a.subject_id == subject_1 for a in g1_s1_list)

    # List all
    all_list = store.list_all()
    assert len(all_list) == 4
