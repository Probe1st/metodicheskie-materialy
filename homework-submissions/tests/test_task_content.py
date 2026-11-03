import stat
from pathlib import Path
from uuid import uuid4

import pytest
from app.assignments import AssignmentStore
from app.task_content import (
    TaskContentStore,
    TaskContentValidationError,
)


@pytest.fixture
def task_store(tmp_path: Path) -> TaskContentStore:
    db_path = tmp_path / ".private" / "access.sqlite3"
    attachments_dir = tmp_path / ".private" / "task-attachments"
    return TaskContentStore(db_path, attachments_dir)


def test_task_store_permissions(tmp_path: Path):
    db_path = tmp_path / ".private" / "access.sqlite3"
    attachments_dir = tmp_path / ".private" / "task-attachments"
    TaskContentStore(db_path, attachments_dir)

    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(attachments_dir.stat().st_mode) == 0o700


def test_set_and_get_content(task_store: TaskContentStore):
    topic_id = uuid4()
    item, orphans = task_store.set_content(
        owner_kind="topic",
        owner_id=topic_id,
        description="# Задание темы",
        description_format="md",
        attachment_id=None,
    )
    assert orphans == []
    assert item.owner_kind == "topic"
    assert item.owner_id == topic_id
    assert item.description == "# Задание темы"
    assert item.description_format == "md"
    assert item.attachment_id is None
    assert item.updated_at is not None

    fetched = task_store.get("topic", topic_id)
    assert fetched == item

    # None for non-existent
    assert task_store.get("topic", uuid4()) is None
    assert task_store.get("lesson", topic_id) is None


def test_validations(task_store: TaskContentStore):
    uid = uuid4()
    with pytest.raises(TaskContentValidationError):
        task_store.set_content("invalid_kind", uid, "text", "md", None)

    with pytest.raises(TaskContentValidationError):
        task_store.set_content("topic", uid, "text", "invalid_format", None)

    with pytest.raises(TaskContentValidationError):
        # attachment_id does not exist in attachments table
        task_store.set_content("topic", uid, "text", "md", uuid4())


def test_save_attachment_and_link(task_store: TaskContentStore):
    data = b"Hello task attachment bytes"
    att = task_store.save_attachment("file.txt", data)
    assert att.original_name == "file.txt"
    assert att.size_bytes == len(data)

    file_path = task_store.get_attachment_path(att.id)
    assert file_path.is_file()
    assert file_path.read_bytes() == data
    assert stat.S_IMODE(file_path.stat().st_mode) == 0o600

    topic_id = uuid4()
    content, _ = task_store.set_content(
        owner_kind="topic",
        owner_id=topic_id,
        description="See attached",
        description_format="txt",
        attachment_id=att.id,
    )
    assert content.attachment_id == att.id

    fetched_att = task_store.get_attachment(att.id)
    assert fetched_att == att


def test_shared_immutable_attachment_refcount(task_store: TaskContentStore):
    # 1. Save attachment A
    bytes_a = b"Original shared task"
    att_a = task_store.save_attachment("task_a.txt", bytes_a)
    path_a = task_store.get_attachment_path(att_a.id)
    assert path_a.is_file()

    # 2. Topic has attachment A
    topic_id = uuid4()
    task_store.set_content("topic", topic_id, "Topic desc", "txt", att_a.id)

    # 3. Lesson 1 and Lesson 2 copy attachment A
    lesson_1 = uuid4()
    lesson_2 = uuid4()
    task_store.set_content("lesson", lesson_1, "Lesson 1 desc", "txt", att_a.id)
    task_store.set_content("lesson", lesson_2, "Lesson 2 desc", "txt", att_a.id)

    # 4. Lesson 1 replaces file with attachment B
    bytes_b = b"Lesson 1 custom file"
    att_b = task_store.save_attachment("task_b.txt", bytes_b)
    path_b = task_store.get_attachment_path(att_b.id)
    task_store.set_content("lesson", lesson_1, "Lesson 1 updated", "txt", att_b.id)

    # Attachment A is still referenced by Topic and Lesson 2! Must NOT be deleted
    assert path_a.is_file()
    assert task_store.get_attachment(att_a.id) is not None

    # 5. Delete topic content
    task_store.delete_content("topic", topic_id)
    # Attachment A is still referenced by Lesson 2! Must NOT be deleted
    assert path_a.is_file()
    assert task_store.get_attachment(att_a.id) is not None

    # 6. Delete lesson 2 content
    task_store.delete_content("lesson", lesson_2)
    # Attachment A now has 0 references! Must be deleted from DB and unlinked from disk
    assert not path_a.is_file()
    assert task_store.get_attachment(att_a.id) is None

    # Attachment B is still referenced by Lesson 1
    assert path_b.is_file()
    assert task_store.get_attachment(att_b.id) is not None

    # 7. Delete lesson 1 content
    task_store.delete_content("lesson", lesson_1)
    # Attachment B now deleted
    assert not path_b.is_file()
    assert task_store.get_attachment(att_b.id) is None


def test_bulk_delete_contents_for_owners(task_store: TaskContentStore):
    att = task_store.save_attachment("doc.pdf", b"PDF content")
    path = task_store.get_attachment_path(att.id)

    l1 = uuid4()
    l2 = uuid4()
    task_store.set_content("lesson", l1, "L1", "txt", att.id)
    task_store.set_content("lesson", l2, "L2", "txt", att.id)

    assert path.is_file()
    task_store.delete_contents_for_owners("lesson", [l1, l2])

    assert task_store.get("lesson", l1) is None
    assert task_store.get("lesson", l2) is None
    assert not path.is_file()
    assert task_store.get_attachment(att.id) is None


def test_shared_transaction_with_assignment_store(tmp_path: Path):
    db_path = tmp_path / ".private" / "access.sqlite3"
    att_dir = tmp_path / ".private" / "task-attachments"

    as_store = AssignmentStore(db_path)
    tc_store = TaskContentStore(db_path, att_dir)

    sub_id = uuid4()
    top_id = uuid4()

    # Successful atomic commit
    with as_store.connect() as conn:
        assignment = as_store.create(
            group_name="ИС 24/9-1",
            subject_id=sub_id,
            topic_id=top_id,
            lesson_date="2026-10-15",
            pair_number=1,
            conn=conn,
        )
        tc_store.set_content(
            owner_kind="lesson",
            owner_id=assignment.id,
            description="Transaction test",
            description_format="txt",
            attachment_id=None,
            conn=conn,
        )
        conn.commit()

    assert as_store.get(assignment.id) is not None
    assert tc_store.get("lesson", assignment.id) is not None

    # Rollback test: nothing is saved
    try:
        with as_store.connect() as conn:
            as_store.create(
                group_name="ИС 24/9-1",
                subject_id=sub_id,
                topic_id=top_id,
                lesson_date="2026-10-15",
                pair_number=2,
                conn=conn,
            )
            # Intentionally raise to rollback
            raise RuntimeError("Simulated failure")
    except RuntimeError:
        pass

    # Verify pair 2 was rolled back
    assert len(as_store.list_for_group("ИС 24/9-1")) == 1

def test_external_transaction_rollback_and_commit_cleanup(task_store: TaskContentStore):
    data_orig = b"original file bytes 12345"
    att_orig = task_store.save_attachment("orig.txt", data_orig)
    path_orig = task_store.get_attachment_path(att_orig.id)
    owner_id = uuid4()
    task_store.set_content("lesson", owner_id, "Initial desc", "txt", att_orig.id)
    assert path_orig.is_file()

    # 1. Test set_content rollback preserves file, metadata and content
    data_new = b"new file bytes 67890"
    with task_store._connect() as conn:
        att_new = task_store.save_attachment("new.txt", data_new, conn=conn)
        updated_item, orphans = task_store.set_content(
            "lesson", owner_id, "Updated desc", "txt", att_new.id, conn=conn
        )
        assert orphans == [att_orig.id]
        # Rollback!
        conn.rollback()
        # Caller would discard uncommitted staged new attachment
        task_store.discard_attachment_file(att_new.id)

    assert path_orig.is_file()
    assert path_orig.read_bytes() == data_orig
    assert task_store.get_attachment(att_orig.id) is not None
    assert task_store.get("lesson", owner_id).description == "Initial desc"
    assert task_store.get("lesson", owner_id).attachment_id == att_orig.id

    # 2. Test set_content commit leaves file until caller discards
    with task_store._connect() as conn:
        att_new2 = task_store.save_attachment("new2.txt", data_new, conn=conn)
        _, orphans2 = task_store.set_content(
            "lesson", owner_id, "Updated desc 2", "txt", att_new2.id, conn=conn
        )
        assert orphans2 == [att_orig.id]
        conn.commit()

    # Before caller explicitly unlinks, file is still on disk!
    assert path_orig.is_file()
    # Now caller cleans up returned orphans
    for o_id in orphans2:
        task_store.discard_attachment_file(o_id)
    assert not path_orig.is_file()
    assert task_store.get_attachment(att_orig.id) is None

    # 3. Test delete_content rollback preserves file, metadata and content
    path_new2 = task_store.get_attachment_path(att_new2.id)
    assert path_new2.is_file()
    with task_store._connect() as conn:
        orphans_del = task_store.delete_content("lesson", owner_id, conn=conn)
        assert orphans_del == [att_new2.id]
        conn.rollback()

    assert path_new2.is_file()
    assert path_new2.read_bytes() == data_new
    assert task_store.get_attachment(att_new2.id) is not None
    assert task_store.get("lesson", owner_id) is not None

    # 4. Test delete_content commit leaves file until explicit discard
    with task_store._connect() as conn:
        orphans_del2 = task_store.delete_content("lesson", owner_id, conn=conn)
        assert orphans_del2 == [att_new2.id]
        conn.commit()
    assert path_new2.is_file()
    for o_id in orphans_del2:
        task_store.discard_attachment_file(o_id)
    assert not path_new2.is_file()
    assert task_store.get_attachment(att_new2.id) is None

    # 5. Test delete_contents_for_owners rollback and commit
    att_multi = task_store.save_attachment("multi.txt", b"multi bytes")
    path_multi = task_store.get_attachment_path(att_multi.id)
    m1 = uuid4()
    m2 = uuid4()
    task_store.set_content("lesson", m1, "M1", "txt", att_multi.id)
    task_store.set_content("lesson", m2, "M2", "txt", att_multi.id)

    # Rollback
    with task_store._connect() as conn:
        orphans_multi = task_store.delete_contents_for_owners("lesson", [m1, m2], conn=conn)
        assert orphans_multi == [att_multi.id]
        conn.rollback()
    assert path_multi.is_file()
    assert task_store.get("lesson", m1) is not None
    assert task_store.get_attachment(att_multi.id) is not None

    # Commit
    with task_store._connect() as conn:
        orphans_multi2 = task_store.delete_contents_for_owners("lesson", [m1, m2], conn=conn)
        assert orphans_multi2 == [att_multi.id]
        conn.commit()
    assert path_multi.is_file()
    for o_id in orphans_multi2:
        task_store.discard_attachment_file(o_id)
    assert not path_multi.is_file()
    assert task_store.get_attachment(att_multi.id) is None
