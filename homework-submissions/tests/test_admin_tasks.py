import io
from uuid import uuid4

from app.catalog_store import create_catalog_store


def test_admin_new_and_edit_assignment_pages(client_env):
    client, _ = client_env
    assert client.get("/admin/assignments/new").status_code == 403

    client.post("/admin/login", data={"password": "admin-secret"})
    resp_new = client.get("/admin/assignments/new")
    assert resp_new.status_code == 200
    assert "Новое занятие с заданием" in resp_new.get_data(as_text=True)
    assert "Взять задание темы" in resp_new.get_data(as_text=True)

    # 404 for non-existent edit
    assert client.get(f"/admin/assignments/{uuid4()}/edit").status_code == 404

    # Create assignment to test edit page
    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0].name
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    create_resp = client.post(
        "/admin/assignments",
        data={
            "group_name": group,
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-20",
            "pair_number": "1",
            "description": "Первое задание",
            "description_format": "txt",
        },
    )
    assert create_resp.status_code == 201
    assign_id = create_resp.json["id"]

    resp_edit = client.get(f"/admin/assignments/{assign_id}/edit")
    assert resp_edit.status_code == 200
    assert "Редактирование занятия и задания" in resp_edit.get_data(as_text=True)
    assert "Первое задание" in resp_edit.get_data(as_text=True)


def test_admin_task_preview(client_env):
    client, _ = client_env
    assert client.post("/admin/tasks/preview", data={"description": "hi", "description_format": "txt"}).status_code == 403

    client.post("/admin/login", data={"password": "admin-secret"})
    resp = client.post(
        "/admin/tasks/preview",
        data={"description": "# Заголовок\n\n[Ссылка](https://example.com)", "description_format": "md"},
    )
    assert resp.status_code == 200
    html = resp.json["html"]
    assert "<h1>Заголовок</h1>" in html
    assert '<a href="https://example.com"' in html

    # Invalid format returns 400
    assert client.post("/admin/tasks/preview", data={"description": "hi", "description_format": "bad"}).status_code == 400


def test_topic_task_crud_and_attachments(client_env):
    client, _ = client_env
    catalog = create_catalog_store(client.application.config).snapshot()
    topic = catalog.topics[0]

    # 403 without admin
    assert client.get(f"/admin/topics/{topic.id}/task").status_code == 403

    client.post("/admin/login", data={"password": "admin-secret"})

    # JSON accept when no task exists
    empty_json = client.get(f"/admin/topics/{topic.id}/task", headers={"Accept": "application/json"})
    assert empty_json.status_code == 200
    assert empty_json.json["has_task"] is False

    # Save topic task with file
    file_bytes = b"Sample topic material bytes"
    save_resp = client.post(
        f"/admin/topics/{topic.id}/task",
        data={
            "description": "Задание для темы в **Markdown**",
            "description_format": "md",
            "attachment": (io.BytesIO(file_bytes), "material.txt"),
        },
        content_type="multipart/form-data",
    )
    assert save_resp.status_code in (200, 302)

    # Check via JSON
    filled_json = client.get(f"/admin/topics/{topic.id}/task", headers={"Accept": "application/json"})
    assert filled_json.status_code == 200
    assert filled_json.json["has_task"] is True
    assert filled_json.json["description"] == "Задание для темы в **Markdown**"
    assert filled_json.json["description_format"] == "md"
    assert filled_json.json["attachment"]["original_name"] == "material.txt"
    att_id = filled_json.json["attachment"]["id"]

    # Download attachment
    client.post("/admin/logout")
    # Unauthorized download
    assert client.get(f"/admin/tasks/attachments/{att_id}").status_code == 403
    client.post("/admin/login", data={"password": "admin-secret"})
    dl_resp = client.get(f"/admin/tasks/attachments/{att_id}")
    assert dl_resp.status_code == 200
    assert dl_resp.data == file_bytes
    assert dl_resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_copy_topic_task_to_lesson_and_file_sharing(client_env):
    client, _ = client_env
    client.post("/admin/login", data={"password": "admin-secret"})
    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0].name
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # 1. Setup topic task with an attachment
    file_bytes = b"Shared topic exercise"
    client.post(
        f"/admin/topics/{topic.id}/task",
        data={
            "description": "Topic assignment text",
            "description_format": "txt",
            "attachment": (io.BytesIO(file_bytes), "exercise.txt"),
        },
        content_type="multipart/form-data",
    )
    topic_data = client.get(f"/admin/topics/{topic.id}/task", headers={"Accept": "application/json"}).json
    topic_att_id = topic_data["attachment"]["id"]

    # 2. Attempting arbitrary unauthorized attachment_id returns 400
    fake_att = str(uuid4())
    bad_resp = client.post(
        "/admin/assignments",
        data={
            "group_name": group,
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-21",
            "pair_number": "1",
            "description": "Hacked",
            "description_format": "txt",
            "attachment_id": fake_att,
        },
    )
    assert bad_resp.status_code == 400

    # 3. Create lesson using the topic task's attachment_id
    create_resp = client.post(
        "/admin/assignments",
        data={
            "group_name": group,
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-21",
            "pair_number": "1",
            "description": "Topic assignment text modified for lesson 1",
            "description_format": "txt",
            "attachment_id": topic_att_id,
        },
    )
    assert create_resp.status_code == 201
    lesson_id = create_resp.json["id"]

    # 4. Check attachment is still downloadable
    dl_resp = client.get(f"/admin/tasks/attachments/{topic_att_id}")
    assert dl_resp.status_code == 200
    assert dl_resp.data == file_bytes

    # 5. Update lesson with a custom new file
    custom_bytes = b"Custom lesson 1 file"
    update_resp = client.post(
        f"/admin/assignments/{lesson_id}/update",
        data={
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-21",
            "pair_number": "1",
            "description": "Lesson 1 with new file",
            "description_format": "txt",
            "attachment": (io.BytesIO(custom_bytes), "custom.txt"),
        },
        content_type="multipart/form-data",
    )
    assert update_resp.status_code == 200

    # The topic still has topic_att_id, so topic file MUST still exist!
    assert client.get(f"/admin/tasks/attachments/{topic_att_id}").status_code == 200


def test_conflicting_attachment_commands_rejected(client_env):
    client, _ = client_env
    client.post("/admin/login", data={"password": "admin-secret"})
    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0].name
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # remove_attachment=1 AND new file simultaneously -> 400
    resp = client.post(
        "/admin/assignments",
        data={
            "group_name": group,
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-22",
            "pair_number": "1",
            "remove_attachment": "1",
            "attachment": (io.BytesIO(b"conflict"), "conflict.txt"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_atomic_conflict_discards_staged_upload(client_env):
    client, private_dir = client_env
    client.post("/admin/login", data={"password": "admin-secret"})
    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0].name
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # 1. Create assignment on pair 1
    r1 = client.post(
        "/admin/assignments",
        data={
            "group_name": group,
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-23",
            "pair_number": "1",
            "description": "Original",
            "description_format": "txt",
        },
    )
    assert r1.status_code == 201

    # Check files in task-attachments directory before conflict
    att_dir = private_dir / "task-attachments"
    files_before = set(att_dir.iterdir()) if att_dir.is_dir() else set()

    # 2. Attempt to create conflicting assignment on same group, date, pair with a file
    r2 = client.post(
        "/admin/assignments",
        data={
            "group_name": group,
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-23",
            "pair_number": "1",
            "description": "Duplicate",
            "description_format": "txt",
            "attachment": (io.BytesIO(b"staged upload bytes"), "test.txt"),
        },
        content_type="multipart/form-data",
    )
    assert r2.status_code == 409
    assert r2.json["error"] == "assignment_conflict"

    # Verify no new physical file remained on disk!
    files_after = set(att_dir.iterdir()) if att_dir.is_dir() else set()
    assert files_after == files_before

def test_rejected_subject_deletion_preserves_data_and_cascade_cleanup(client_env):
    from app.assignments import AssignmentStore
    from app.task_content import TaskContentStore

    client, private_dir = client_env
    client.post("/admin/login", data={"password": "admin-secret"})

    as_store = AssignmentStore(private_dir / "access.sqlite3")
    tc_store = TaskContentStore(private_dir / "access.sqlite3", private_dir / "task-attachments")

    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0].name
    unassigned_id = catalog.unassigned_subject_id

    # 1. Create a subject under test
    resp_s = client.post("/admin/subjects", data={"name": "Предмет Конфликт"})
    assert resp_s.status_code == 201
    subj_id = resp_s.json["id"]

    # 2. In unassigned_subject_id, create topic "Дубликат"
    resp_top_un = client.post(
        "/admin/topics",
        data={"name": "Дубликат", "subject_id": str(unassigned_id)},
    )
    assert resp_top_un.status_code == 201
    unassigned_topic_id = resp_top_un.json["id"]

    # 3. Under subj_id, create topic "Дубликат"
    resp_top_subj = client.post(
        "/admin/topics",
        data={"name": "Дубликат", "subject_id": subj_id},
    )
    assert resp_top_subj.status_code == 201
    conflict_topic_id = resp_top_subj.json["id"]

    # 4. Attach template file to the topic task
    topic_bytes = b"Topic template unique content 112233"
    resp_topic_task = client.post(
        f"/admin/topics/{conflict_topic_id}/task",
        data={
            "description": "Topic assignment task",
            "description_format": "txt",
            "attachment": (io.BytesIO(topic_bytes), "topic_file.txt"),
        },
        content_type="multipart/form-data",
    )
    assert resp_topic_task.status_code in (200, 302)
    tc_topic = tc_store.get("topic", conflict_topic_id)
    assert tc_topic is not None and tc_topic.attachment_id is not None
    topic_att_id = tc_topic.attachment_id
    topic_att_path = tc_store.get_attachment_path(topic_att_id)
    assert topic_att_path.is_file()
    assert topic_att_path.read_bytes() == topic_bytes

    # 5. Create an assignment for this topic with its OWN separate file
    lesson_bytes = b"Lesson unique separate file content 445566"
    resp_lesson = client.post(
        "/admin/assignments",
        data={
            "group_name": group,
            "subject_id": subj_id,
            "topic_id": conflict_topic_id,
            "lesson_date": "2026-11-10",
            "pair_number": "1",
            "description": "Lesson 1 assignment",
            "description_format": "txt",
            "attachment": (io.BytesIO(lesson_bytes), "lesson_file.txt"),
        },
        content_type="multipart/form-data",
    )
    assert resp_lesson.status_code == 201
    assign_id = resp_lesson.json["id"]
    tc_lesson = tc_store.get("lesson", assign_id)
    assert tc_lesson is not None and tc_lesson.attachment_id is not None
    lesson_att_id = tc_lesson.attachment_id
    lesson_att_path = tc_store.get_attachment_path(lesson_att_id)
    assert lesson_att_path.is_file()
    assert lesson_att_path.read_bytes() == lesson_bytes

    # 6. Attempt deleting the subject -> must return 400 with topic_name_conflict because
    # moving topic "Дубликат" to "Без предмета" causes a name conflict!
    del_resp = client.post(f"/admin/subjects/{subj_id}/delete")
    assert del_resp.status_code == 400
    assert del_resp.json["error"] == "topic_name_conflict"
    assert del_resp.json["names"] == ["Дубликат"]
    # Verification: catalog, assignment, task contents, metadata, and exact file bytes are INTACT!
    assert as_store.get(assign_id) is not None
    assert tc_store.get("lesson", assign_id) is not None
    assert tc_store.get("topic", conflict_topic_id) is not None
    assert lesson_att_path.is_file()
    assert lesson_att_path.read_bytes() == lesson_bytes
    assert topic_att_path.is_file()
    assert topic_att_path.read_bytes() == topic_bytes

    # 7. Resolve conflict by renaming unassigned topic
    rename_resp = client.post(
        f"/admin/topics/{unassigned_topic_id}/update",
        data={"name": "Неконфликтная тема", "subject_id": str(unassigned_id)},
    )
    assert rename_resp.status_code == 200

    # 8. Add a second assignment that SHARES the topic's attachment file
    resp_lesson_shared = client.post(
        "/admin/assignments",
        data={
            "group_name": group,
            "subject_id": subj_id,
            "topic_id": conflict_topic_id,
            "lesson_date": "2026-11-10",
            "pair_number": "2",
            "description": "Lesson 2 sharing topic file",
            "description_format": "txt",
            "attachment_id": str(topic_att_id),
        },
        content_type="multipart/form-data",
    )
    assert resp_lesson_shared.status_code == 201
    assign_shared_id = resp_lesson_shared.json["id"]

    # 9. Now delete the subject -> 204 success!
    del_success = client.post(f"/admin/subjects/{subj_id}/delete")
    assert del_success.status_code == 204

    # Verification after successful delete:
    # - Both assignments are deleted
    assert as_store.get(assign_id) is None
    assert as_store.get(assign_shared_id) is None
    assert tc_store.get("lesson", assign_id) is None
    assert tc_store.get("lesson", assign_shared_id) is None

    # - Unique lesson attachment file is deleted from disk and DB
    assert not lesson_att_path.is_file()
    assert tc_store.get_attachment(lesson_att_id) is None

    # - Shared/topic attachment file is NOT deleted (still exists on disk and in DB)!
    assert topic_att_path.is_file()
    assert topic_att_path.read_bytes() == topic_bytes
    assert tc_store.get_attachment(topic_att_id) is not None

    # - Topic itself and its task template are preserved under "Без предмета"
    cat_after = create_catalog_store(client.application.config).snapshot()
    preserved_topic = next((t for t in cat_after.topics if str(t.id) == conflict_topic_id), None)
    assert preserved_topic is not None
    assert preserved_topic.subject_id == unassigned_id
    assert tc_store.get("topic", conflict_topic_id) is not None

    # - Downloading the preserved topic attachment works
    dl_resp = client.get(f"/admin/tasks/attachments/{topic_att_id}")
    assert dl_resp.status_code == 200
    assert dl_resp.data == topic_bytes
