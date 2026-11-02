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
