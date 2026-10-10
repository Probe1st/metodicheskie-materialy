import stat
from pathlib import Path
from uuid import uuid4

import pytest

from app.assignments import (
    AssignmentConflictError,
    AssignmentNotFoundError,
    AssignmentStore,
    AssignmentValidationError,
)


@pytest.fixture
def store(tmp_path: Path) -> AssignmentStore:
    return AssignmentStore(tmp_path / ".private" / "assignments.sqlite3")


def test_private_permissions(tmp_path: Path) -> None:
    database = tmp_path / ".private" / "assignments.sqlite3"
    AssignmentStore(database)
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_create_and_get_assignment(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    item = store.create(
        group_name="ИС 25/9-1П",
        subject_id=subject_id,
        topic_id=topic_id,
        lesson_date="2026-10-14",
        pair_number=2,
    )
    assert item.group_name == "ИС 25/9-1П"
    assert item.subject_id == subject_id
    assert item.topic_id == topic_id
    assert item.lesson_date == "2026-10-14"
    assert item.pair_number == 2
    assert item.created_at is not None
    assert item.updated_at is not None

    fetched = store.get(item.id)
    assert fetched == item

    non_existent = store.get(uuid4())
    assert non_existent is None


def test_unique_group_date_pair_constraint(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id_1 = uuid4()
    topic_id_2 = uuid4()

    store.create("ИС 25/9-1П", subject_id, topic_id_1, "2026-10-14", 2)
    with pytest.raises(AssignmentConflictError):
        store.create("ИС 25/9-1П", subject_id, topic_id_2, "2026-10-14", 2)


def test_pair_number_boundary_validation(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "2026-10-14", 0)

    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "2026-10-14", 7)

    item = store.create("ИС 25/9-1П", subject_id, topic_id, "2026-10-14", 1)
    with pytest.raises(AssignmentValidationError):
        store.update(item.id, "2026-10-14", 0, topic_id)

    with pytest.raises(AssignmentValidationError):
        store.update(item.id, "2026-10-14", 7, topic_id)


def test_lesson_date_format_validation(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "14-10-2026", 1)

    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "2026/10/14", 1)

    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "2026-02-31", 1)

    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "invalid", 1)

    item = store.create("ИС 25/9-1П", subject_id, topic_id, "2026-10-14", 1)
    with pytest.raises(AssignmentValidationError):
        store.update(item.id, "not-a-date", 1, topic_id)


def test_group_name_validation(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    with pytest.raises(AssignmentValidationError):
        store.create("", subject_id, topic_id, "2026-10-14", 1)

    with pytest.raises(AssignmentValidationError):
        store.create("   ", subject_id, topic_id, "2026-10-14", 1)


def test_update_assignment_success_and_conflict(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id_1 = uuid4()
    topic_id_2 = uuid4()

    first = store.create("ИС 25/9-1П", subject_id, topic_id_1, "2026-10-14", 1)
    store.create("ИС 25/9-1П", subject_id, topic_id_2, "2026-10-14", 2)

    updated = store.update(first.id, "2026-10-15", 3, topic_id_1)
    assert updated.lesson_date == "2026-10-15"
    assert updated.pair_number == 3
    assert updated.updated_at >= first.updated_at

    with pytest.raises(AssignmentConflictError):
        store.update(first.id, "2026-10-14", 2, topic_id_1)

    with pytest.raises(AssignmentNotFoundError):
        store.update(uuid4(), "2026-10-14", 4, topic_id_1)


def test_delete_assignment_idempotent(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    item = store.create("ИС 25/9-1П", subject_id, topic_id, "2026-10-14", 1)
    store.delete(item.id)
    assert store.get(item.id) is None

    # Deleting again must be idempotent and succeed without error
    store.delete(item.id)
    store.delete(uuid4())


def test_list_for_group_and_subject_ordering(store: AssignmentStore) -> None:
    subject_1 = uuid4()
    subject_2 = uuid4()
    t1, t2, t3, t4 = uuid4(), uuid4(), uuid4(), uuid4()

    # Create items with different dates and pair numbers to test:
    # ORDER BY lesson_date DESC, pair_number ASC
    store.create("ИС 25/9-1П", subject_1, t1, "2026-10-10", 2)
    store.create("ИС 25/9-1П", subject_1, t2, "2026-10-14", 2)
    store.create("ИС 25/9-1П", subject_1, t4, "2026-10-14", 1)
    store.create("ИС 25/9-1П", subject_2, t3, "2026-10-12", 3)
    store.create("ИС 24/9-3П", subject_1, t1, "2026-10-14", 1)

    group_items = store.list_for_group("ИС 25/9-1П")
    assert len(group_items) == 4
    # Expected ordering:
    # 2026-10-14 pair 1, 2026-10-14 pair 2, 2026-10-12 pair 3, 2026-10-10 pair 2
    assert [(x.lesson_date, x.pair_number) for x in group_items] == [
        ("2026-10-14", 1),
        ("2026-10-14", 2),
        ("2026-10-12", 3),
        ("2026-10-10", 2),
    ]

    subject_items = store.list_for_group_and_subject("ИС 25/9-1П", subject_1)
    assert len(subject_items) == 3
    assert [(x.lesson_date, x.pair_number) for x in subject_items] == [
        ("2026-10-14", 1),
        ("2026-10-14", 2),
        ("2026-10-10", 2),
    ]


def test_rename_and_delete_group_cascades(store: AssignmentStore) -> None:
    subject_id = uuid4()
    t1 = uuid4()
    store.create("ИС 25/9-1П", subject_id, t1, "2026-10-14", 1)

    store.rename_group("ИС 25/9-1П", "ИС 25-Новая")
    assert len(store.list_for_group("ИС 25/9-1П")) == 0
    assert len(store.list_for_group("ИС 25-Новая")) == 1

    store.delete_group("ИС 25-Новая")
    assert len(store.list_for_group("ИС 25-Новая")) == 0
