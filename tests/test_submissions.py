"""Comprehensive tests for submissions store, student management, and safe storage."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import pytest

from app.database import connect, initialize
from app.submissions import (
    SubmissionAccessError,
    SubmissionError,
    SubmissionNotFoundError,
    SubmissionStore,
    SubmissionValidationError,
)


@pytest.fixture
def store_env(tmp_path: Path) -> tuple[SubmissionStore, Path, Path]:
    db_path = tmp_path / "test.db"
    storage_root = tmp_path / "storage"
    initialize(db_path)
    storage_root.mkdir(parents=True, exist_ok=True)
    store = SubmissionStore(db_path, storage_root)
    return store, db_path, storage_root


def _create_sample_group(db_path: Path, name: str = "ИС 25/9-1П", is_open: int = 1, version: int = 1) -> str:
    group_id = str(uuid4())
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO groups (id, name, name_key) VALUES (?, ?, ?)",
            (group_id, name, name.lower()),
        )
        conn.execute(
            "INSERT INTO group_access (group_id, is_open, version) VALUES (?, ?, ?)",
            (group_id, is_open, version),
        )
    return group_id


def _create_sample_topic_and_assignment(
    db_path: Path,
    group_id: str,
    topic_name: str | None = None,
    lesson_date: str = "2026-11-03",
    pair_number: int = 1,
) -> tuple[str, str]:
    if topic_name is None:
        topic_name = f"Topic {uuid4().hex[:8]}"

    with connect(db_path) as conn:
        subj_row = conn.execute("SELECT id FROM subjects WHERE is_system = 1").fetchone()
        subject_id = subj_row["id"]
        topic_id = str(uuid4())
        conn.execute(
            "INSERT INTO topics (id, subject_id, name, name_key) VALUES (?, ?, ?, ?)",
            (topic_id, subject_id, topic_name, topic_name.lower()),
        )
        assignment_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '2026-11-03T10:00:00+00:00', '2026-11-03T10:00:00+00:00')
            """,
            (assignment_id, group_id, topic_id, lesson_date, pair_number),
        )
    return topic_id, assignment_id


def test_student_creation(store_env: tuple[SubmissionStore, Path, Path]) -> None:
    store, db_path, _ = store_env
    group_id = UUID(_create_sample_group(db_path))

    # 1. Empty or whitespace name rejected
    with pytest.raises(SubmissionValidationError, match="display_name cannot be empty"):
        store.create_student(group_id, "")

    with pytest.raises(SubmissionValidationError, match="display_name cannot be empty"):
        store.create_student(group_id, "   \t\n  ")

    # 2. Duplicate display names permitted with distinct UUIDs
    student1 = store.create_student(group_id, "Иванов Иван")
    student2 = store.create_student(group_id, "Иванов Иван")

    assert student1.id != student2.id
    assert student1.group_id == group_id
    assert student2.group_id == group_id
    assert student1.display_name == "Иванов Иван"
    assert student2.display_name == "Иванов Иван"

    students = store.list_students(group_id)
    assert len(students) == 2
    ids = {s.id for s in students}
    assert ids == {student1.id, student2.id}

    # 3. Non-existent group raises SubmissionNotFoundError
    with pytest.raises(SubmissionNotFoundError):
        store.create_student(uuid4(), "Петров Петр")

    # 4. Archived group raises SubmissionValidationError
    archived_group_id = str(uuid4())
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO groups (id, name, name_key, archived_at) VALUES (?, 'Archived', 'archived', '2026-11-03T10:00:00+00:00')",
            (archived_group_id,),
        )
    with pytest.raises(SubmissionValidationError, match="is archived"):
        store.create_student(UUID(archived_group_id), "Петров Петр")


def test_submit_happy_path(store_env: tuple[SubmissionStore, Path, Path]) -> None:
    store, db_path, storage_root = store_env
    group_id = UUID(_create_sample_group(db_path))
    student = store.create_student(group_id, "Сидоров Сидор")
    topic_id_str, assignment_id_str = _create_sample_topic_and_assignment(db_path, str(group_id), topic_name="Тема 1")
    assignment_id = UUID(assignment_id_str)
    topic_id = UUID(topic_id_str)

    # First attempt
    content1 = b"def solution_v1():\n    return 42\n"
    sub1, file1 = store.submit(
        group_id=group_id,
        student_id=student.id,
        assignment_id=assignment_id,
        source=io.BytesIO(content1),
        original_filename="solution.py",
        access_version=1,
    )

    assert sub1.attempt_number == 1
    assert sub1.review_status == "pending"
    assert sub1.reviewed_at is None
    assert sub1.topic_id == topic_id
    assert sub1.student_id == student.id
    assert file1.size_bytes == len(content1)
    assert file1.stored_path == f"{group_id}/{topic_id}/{sub1.id}.py"

    full_path1 = storage_root / str(group_id) / str(topic_id) / f"{sub1.id}.py"
    assert full_path1.is_file()
    assert full_path1.read_bytes() == content1

    # Second attempt for same student & topic
    content2 = b"def solution_v2():\n    return 100\n"
    sub2, file2 = store.submit(
        group_id=group_id,
        student_id=student.id,
        assignment_id=assignment_id,
        source=io.BytesIO(content2),
        original_filename="solution_final.PY",
        access_version=1,
    )

    assert sub2.attempt_number == 2
    assert sub2.id != sub1.id
    assert file2.size_bytes == len(content2)
    assert file2.stored_path == f"{group_id}/{topic_id}/{sub2.id}.py"

    full_path2 = storage_root / str(group_id) / str(topic_id) / f"{sub2.id}.py"
    assert full_path2.is_file()
    assert full_path2.read_bytes() == content2

    # Check list_submissions returns both
    submissions = store.list_submissions(group_id=group_id)
    assert len(submissions) == 2
    assert submissions[0].id == sub2.id
    assert submissions[1].id == sub1.id
    assert submissions[0].student_display_name == "Сидоров Сидор"
    assert submissions[0].topic_name == "Тема 1"


def test_submit_pre_check_failures(store_env: tuple[SubmissionStore, Path, Path]) -> None:
    store, db_path, _ = store_env
    group_id = UUID(_create_sample_group(db_path))
    student = store.create_student(group_id, "Тестовый Студент")
    _, assignment_id_str = _create_sample_topic_and_assignment(db_path, str(group_id))
    assignment_id = UUID(assignment_id_str)

    # 1. Closed access
    with connect(db_path) as conn:
        conn.execute("UPDATE group_access SET is_open = 0 WHERE group_id = ?", (str(group_id),))

    with pytest.raises(SubmissionAccessError, match="Submissions are closed"):
        store.submit(group_id, student.id, assignment_id, io.BytesIO(b"data"), "file.pdf", 1)

    # 2. Expired deadline
    past_utc = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE group_access SET is_open = 1, closes_at_utc = ? WHERE group_id = ?",
            (past_utc, str(group_id)),
        )

    with pytest.raises(SubmissionAccessError, match="deadline has passed"):
        store.submit(group_id, student.id, assignment_id, io.BytesIO(b"data"), "file.pdf", 1)

    # 3. Version mismatch
    with connect(db_path) as conn:
        conn.execute("UPDATE group_access SET closes_at_utc = NULL, version = 2 WHERE group_id = ?", (str(group_id),))

    with pytest.raises(SubmissionAccessError, match="version mismatch"):
        store.submit(group_id, student.id, assignment_id, io.BytesIO(b"data"), "file.pdf", 1)

    # Reset access to valid
    with connect(db_path) as conn:
        conn.execute("UPDATE group_access SET version = 1 WHERE group_id = ?", (str(group_id),))

    # 4. Student from different group
    other_group_id = UUID(_create_sample_group(db_path, name="Другая группа"))
    other_student = store.create_student(other_group_id, "Студент Б")

    with pytest.raises(SubmissionAccessError, match="does not belong to group"):
        store.submit(group_id, other_student.id, assignment_id, io.BytesIO(b"data"), "file.pdf", 1)

    # 5. Assignment from different group
    _, other_assignment_id_str = _create_sample_topic_and_assignment(db_path, str(other_group_id))
    other_assignment_id = UUID(other_assignment_id_str)

    with pytest.raises(SubmissionAccessError, match="does not belong to group"):
        store.submit(group_id, student.id, other_assignment_id, io.BytesIO(b"data"), "file.pdf", 1)

    # 6. Group archived
    with connect(db_path) as conn:
        conn.execute("UPDATE groups SET archived_at = '2026-11-03T00:00:00+00:00' WHERE id = ?", (str(group_id),))
    with pytest.raises(SubmissionAccessError, match="archived"):
        store.submit(group_id, student.id, assignment_id, io.BytesIO(b"data"), "file.pdf", 1)
    with connect(db_path) as conn:
        conn.execute("UPDATE groups SET archived_at = NULL WHERE id = ?", (str(group_id),))

    # 7. Student archived
    with connect(db_path) as conn:
        conn.execute("UPDATE students SET archived_at = '2026-11-03T00:00:00+00:00' WHERE id = ?", (str(student.id),))
    with pytest.raises(SubmissionAccessError, match="archived"):
        store.submit(group_id, student.id, assignment_id, io.BytesIO(b"data"), "file.pdf", 1)
    with connect(db_path) as conn:
        conn.execute("UPDATE students SET archived_at = NULL WHERE id = ?", (str(student.id),))

    # 8. Assignment archived
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE class_assignments SET archived_at = '2026-11-03T00:00:00+00:00' WHERE id = ?",
            (str(assignment_id),),
        )
    with pytest.raises(SubmissionAccessError, match="archived"):
        store.submit(group_id, student.id, assignment_id, io.BytesIO(b"data"), "file.pdf", 1)
    with connect(db_path) as conn:
        conn.execute("UPDATE class_assignments SET archived_at = NULL WHERE id = ?", (str(assignment_id),))


def test_submit_rollback_on_error(store_env: tuple[SubmissionStore, Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    store, db_path, storage_root = store_env
    group_id = UUID(_create_sample_group(db_path))
    student = store.create_student(group_id, "Студент Ошибка")
    _, assignment_id_str = _create_sample_topic_and_assignment(db_path, str(group_id))
    assignment_id = UUID(assignment_id_str)

    orig_connect = connect

    class FailingConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            if "INSERT INTO submission_files" in sql:
                raise RuntimeError("Simulated failure during insert")
            return self._conn.execute(sql, params)

        def commit(self):
            return self._conn.commit()

        def rollback(self):
            return self._conn.rollback()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return self._conn.__exit__(exc_type, exc_val, exc_tb)

    from contextlib import contextmanager

    @contextmanager
    def mock_connect(path):
        with orig_connect(path) as real_conn:
            yield FailingConnection(real_conn)

    monkeypatch.setattr("app.submissions.connect", mock_connect)

    with pytest.raises(RuntimeError, match="Simulated failure"):
        store.submit(group_id, student.id, assignment_id, io.BytesIO(b"content"), "doc.txt", 1)

    # Verify temp file cleaned up from .pending
    pending_files = list((storage_root / ".pending").iterdir())
    assert pending_files == []

    # Verify no orphan target files on disk
    with orig_connect(db_path) as conn:
        sub_count = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        assert sub_count == 0
        file_count = conn.execute("SELECT COUNT(*) FROM submission_files").fetchone()[0]
        assert file_count == 0


def test_submit_concurrency(store_env: tuple[SubmissionStore, Path, Path]) -> None:
    store, db_path, storage_root = store_env
    group_id = UUID(_create_sample_group(db_path))
    student = store.create_student(group_id, "Многопоточный Студент")
    _, assignment_id_str = _create_sample_topic_and_assignment(db_path, str(group_id))
    assignment_id = UUID(assignment_id_str)

    results: list[int] = []
    errors: list[Exception] = []

    def run_submit(worker_num: int) -> None:
        try:
            content = f"content from thread {worker_num}".encode()
            sub, _ = store.submit(
                group_id,
                student.id,
                assignment_id,
                io.BytesIO(content),
                f"work_{worker_num}.txt",
                1,
            )
            results.append(sub.attempt_number)
        except (SubmissionError, OSError, RuntimeError) as err:
            errors.append(err)

    t1 = Thread(target=run_submit, args=(1,))
    t2 = Thread(target=run_submit, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
    assert sorted(results) == [1, 2]

    # Verify both files exist
    subs = store.list_submissions(group_id=group_id)
    assert len(subs) == 2
    for s in subs:
        disk_path = storage_root / s.stored_path
        assert disk_path.is_file()


def test_review_status_update(store_env: tuple[SubmissionStore, Path, Path]) -> None:
    store, db_path, _ = store_env
    group_id = UUID(_create_sample_group(db_path))
    student = store.create_student(group_id, "Рецензируемый Студент")
    _, assignment_id_str = _create_sample_topic_and_assignment(db_path, str(group_id))
    assignment_id = UUID(assignment_id_str)

    sub, _ = store.submit(
        group_id,
        student.id,
        assignment_id,
        io.BytesIO(b"my submission"),
        "report.pdf",
        1,
    )
    file_path, _ = store.get_file(sub.id)
    assert file_path.is_file()

    # Mark as reviewed
    status, reviewed_at = store.set_review_status(sub.id, "reviewed")
    assert status == "reviewed"
    assert reviewed_at is not None
    # File is NOT renamed on disk!
    assert file_path.is_file()

    # Re-fetch submission detail
    details = store.list_submissions(group_id=group_id)
    assert details[0].review_status == "reviewed"
    assert details[0].reviewed_at is not None

    # Mark back as pending
    status_p, reviewed_at_p = store.set_review_status(sub.id, "pending")
    assert status_p == "pending"
    assert reviewed_at_p is None
    # File still remains unchanged
    assert file_path.is_file()

    # Invalid status raises SubmissionValidationError
    with pytest.raises(SubmissionValidationError):
        store.set_review_status(sub.id, "invalid_status")

    # Non-existent submission raises SubmissionNotFoundError
    with pytest.raises(SubmissionNotFoundError):
        store.set_review_status(uuid4(), "reviewed")


def test_get_file(store_env: tuple[SubmissionStore, Path, Path]) -> None:
    store, db_path, _ = store_env
    group_id = UUID(_create_sample_group(db_path))
    student = store.create_student(group_id, "Студент Файл")
    _, assignment_id_str = _create_sample_topic_and_assignment(db_path, str(group_id))
    assignment_id = UUID(assignment_id_str)

    content = b"sample pdf content"
    sub, _ = store.submit(
        group_id,
        student.id,
        assignment_id,
        io.BytesIO(content),
        "folder/my_report.PDF",
        1,
    )

    # 1. Normal resolution
    path, original_name = store.get_file(sub.id)
    assert original_name == "my_report.PDF"
    assert path.is_file()
    assert path.read_bytes() == content

    # 2. Submission not found
    with pytest.raises(SubmissionNotFoundError):
        store.get_file(uuid4())

    # 3. File missing on disk
    path.unlink()
    with pytest.raises(FileNotFoundError):
        store.get_file(sub.id)

    # 4. Path traversal attempt via stored_path manipulation in DB
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE submission_files SET stored_path = '../../etc/passwd' WHERE submission_id = ?",
            (str(sub.id),),
        )

    with pytest.raises(ValueError, match="Path escapes storage root"):
        store.get_file(sub.id)
