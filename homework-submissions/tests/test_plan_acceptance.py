import io
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

from app.assignments import AssignmentStore
from app.catalog_store import create_catalog_store
from app.task_content import TaskContentStore
from app.web import create_app
from tests.conftest import configure_group


def test_topic_template_copy_modify_and_isolate_lessons(client_env):
    """Scenario:
    Topic A has Markdown and task.txt with bytes 'original'.
    Create 2 lessons from A.
    Lesson 1 modifies text and replaces file with 'custom_bytes'.
    Lesson 2 and Topic A retain original text and original bytes.
    Modify and delete Topic A's task template: both lessons retain their texts and attachment downloads.
    """
    client, private_dir = client_env
    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0].name
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    client.post("/admin/login", data={"password": "admin-secret"})

    # 1. Topic A task template setup
    orig_text = "# Задание\nПрочитайте [статью](https://example.org)"
    orig_bytes = b"original"
    client.post(
        f"/admin/topics/{topic.id}/task",
        data={
            "description": orig_text,
            "description_format": "md",
            "attachment": (io.BytesIO(orig_bytes), "task.txt"),
        },
        content_type="multipart/form-data",
    )
    topic_json = client.get(f"/admin/topics/{topic.id}/task", headers={"Accept": "application/json"}).json
    orig_att_id = topic_json["attachment"]["id"]

    # 2. Create Lesson 1 and Lesson 2 using Topic A's template
    l1_res = client.post("/admin/assignments", data={
        "group_name": group, "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-10", "pair_number": "1",
        "description": orig_text, "description_format": "md", "attachment_id": orig_att_id,
    })
    assert l1_res.status_code == 201
    l1_id = l1_res.json["id"]

    l2_res = client.post("/admin/assignments", data={
        "group_name": group, "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-10", "pair_number": "2",
        "description": orig_text, "description_format": "md", "attachment_id": orig_att_id,
    })
    assert l2_res.status_code == 201
    l2_id = l2_res.json["id"]

    # 3. Lesson 1 changes text and replaces file
    custom_text = "Изменённый текст первой пары"
    custom_bytes = b"custom_bytes"
    up_res = client.post(
        f"/admin/assignments/{l1_id}/update",
        data={
            "topic_id": str(topic.id), "lesson_date": "2026-11-10", "pair_number": "1",
            "description": custom_text, "description_format": "txt",
            "attachment": (io.BytesIO(custom_bytes), "custom.txt"),
        },
        content_type="multipart/form-data",
    )
    assert up_res.status_code == 200

    # 4. Check Lesson 2 and Topic A still have original text and original bytes
    tc_store = TaskContentStore(private_dir / "access.sqlite3", private_dir / "task-attachments")
    l2_task = tc_store.get("lesson", uuid4() if False else UUID(l2_res.json["id"]))
    assert l2_task is not None
    assert l2_task.description == orig_text
    assert l2_task.description_format == "md"
    assert str(l2_task.attachment_id) == orig_att_id

    topic_task = tc_store.get("topic", topic.id)
    assert topic_task is not None
    assert topic_task.description == orig_text
    assert str(topic_task.attachment_id) == orig_att_id

    # Check downloads
    assert client.get(f"/admin/tasks/attachments/{orig_att_id}").data == orig_bytes

    # 5. Modify and then remove Topic A's task template
    client.post(f"/admin/topics/{topic.id}/task", data={
        "description": "Новый шаблон темы",
        "description_format": "txt",
        "remove_attachment": "1",
    })
    topic_after = tc_store.get("topic", topic.id)
    assert topic_after is not None
    assert topic_after.attachment_id is None

    # Both lessons still have their texts and file downloads intact!
    l1_content = tc_store.get("lesson", UUID(l1_id))
    assert l1_content is not None and l1_content.attachment_id is not None
    l1_data = client.get(f"/admin/tasks/attachments/{l1_content.attachment_id}").data
    assert l1_data == custom_bytes

    l2_data = client.get(f"/admin/tasks/attachments/{orig_att_id}").data
    assert l2_data == orig_bytes
    # Student views both lessons
    configure_group(private_dir, group=group, password="1234", is_open=True)
    client.post("/unlock", data={"group": group, "password": "1234"})

    page_1 = client.get(f"/student/assignments/{l1_id}").get_data(as_text=True)
    assert custom_text in page_1

    page_2 = client.get(f"/student/assignments/{l2_id}").get_data(as_text=True)
    assert "Прочитайте" in page_2
    assert '<a href="https://example.org"' in page_2


def test_legacy_sqlite_db_backward_compatibility(tmp_path: Path):
    """Scenario:
    Legacy SQLite DB with only class_assignments and group_access is opened twice.
    Old rows preserved, new task tables created idempotently, app runs cleanly.
    """
    db_path = tmp_path / ".private" / "access.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Create only legacy tables
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE class_assignments (
            id TEXT PRIMARY KEY,
            group_name TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            lesson_date TEXT NOT NULL,
            pair_number INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    legacy_id = str(uuid4())
    conn.execute("""
        INSERT INTO class_assignments VALUES (?, 'ИС 25/9-1П', ?, ?, '2026-09-01', 1, '2026-09-01T08:00:00Z', '2026-09-01T08:00:00Z')
    """, (legacy_id, str(uuid4()), str(uuid4())))
    conn.commit()
    conn.close()

    catalog_path = tmp_path / "catalog.json"
    repo_catalog = Path(__file__).resolve().parents[1] / "catalog.json"
    if not repo_catalog.is_file():
        repo_catalog = repo_catalog.with_name("catalog.example.json")
    catalog_path.write_bytes(repo_catalog.read_bytes())

    config = {
        "TESTING": True,
        "SECRET_KEY": "secret",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": tmp_path / ".private",
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": catalog_path,
        "MAX_UPLOAD_GB": 1,
    }

    # Open 1st time
    app1 = create_app(config)
    client1 = app1.test_client()
    client1.post("/admin/login", data={"password": "admin-secret"})
    resp1 = client1.get("/admin/assignments")
    assert resp1.status_code == 200

    # Open 2nd time
    app2 = create_app(config)
    client2 = app2.test_client()
    client2.post("/admin/login", data={"password": "admin-secret"})
    resp2 = client2.get("/admin/assignments")
    assert resp2.status_code == 200

    # Check legacy assignment still exists
    store = AssignmentStore(db_path)
    assert store.get(UUID(legacy_id)) is not None


def test_no_task_attachments_stored_in_static_directory(client_env):
    """Ensure no attachment files are written to static/ directory."""
    client, _ = client_env
    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0].name
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    client.post("/admin/login", data={"password": "admin-secret"})
    r = client.post("/admin/assignments", data={
        "group_name": group, "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-12", "pair_number": "1",
        "attachment": (io.BytesIO(b"sensitive content"), "secret.txt"),
    }, content_type="multipart/form-data")
    assert r.status_code == 201

    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    static_files = [f.name for f in static_dir.rglob("*")]
    assert "secret.txt" not in static_files
