import io
from uuid import uuid4

from app.catalog_store import create_catalog_store


def test_student_assignments_listing_has_flags(client_env):
    client, private_dir = client_env
    # 403 when not unlocked
    assert client.get("/student/assignments").status_code == 403

    # Unlock Group A
    from tests.conftest import configure_group
    configure_group(private_dir, group="ИС 25/9-1П", password="1234", is_open=True)
    unlock_res = client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}, headers={"Accept": "application/json"})
    assert unlock_res.status_code == 200

    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # Setup assignments with 4 combinations:
    # 1. No task
    # 2. Text only
    # 3. File only
    # 4. Both
    client.post("/admin/login", data={"password": "admin-secret"})

    # 1. No task
    r1 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-01", "pair_number": "1",
    })
    a1_id = r1.json["id"]

    # 2. Text only
    r2 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-01", "pair_number": "2",
        "description": "Только текст", "description_format": "txt",
    })
    a2_id = r2.json["id"]

    # 3. File only
    r3 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-01", "pair_number": "3",
        "attachment": (io.BytesIO(b"file only"), "file.pdf"),
    }, content_type="multipart/form-data")
    a3_id = r3.json["id"]

    # 4. Both
    r4 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-01", "pair_number": "4",
        "description": "И текст, и файл", "description_format": "md",
        "attachment": (io.BytesIO(b"both bytes"), "both.zip"),
    }, content_type="multipart/form-data")
    a4_id = r4.json["id"]

    # Query as student
    list_res = client.get("/student/assignments")
    assert list_res.status_code == 200
    items = {item["id"]: item for item in list_res.json["assignments"]}

    assert items[a1_id]["has_description"] is False
    assert items[a1_id]["has_attachment"] is False

    assert items[a2_id]["has_description"] is True
    assert items[a2_id]["has_attachment"] is False

    assert items[a3_id]["has_description"] is False
    assert items[a3_id]["has_attachment"] is True

    assert items[a4_id]["has_description"] is True
    assert items[a4_id]["has_attachment"] is True


def test_student_assignment_detail_and_isolation(client_env):
    client, private_dir = client_env
    from tests.conftest import configure_group
    configure_group(private_dir, group="ИС 25/9-1П", password="1234", is_open=True)
    configure_group(private_dir, group="ИС 25/9-2П", password="5678", is_open=True)

    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # Admin creates assignment for Group 1 and Group 2
    client.post("/admin/login", data={"password": "admin-secret"})
    r1 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-02", "pair_number": "1",
        "description": "Задание для группы 1", "description_format": "txt",
    })
    g1_assign_id = r1.json["id"]

    r2 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-2П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-02", "pair_number": "2",
        "description": "Задание для группы 2", "description_format": "txt",
    })
    g2_assign_id = r2.json["id"]

    # Student from Group 1 logs in
    client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})

    # Group 1 can view Group 1 assignment
    res_ok = client.get(f"/student/assignments/{g1_assign_id}")
    assert res_ok.status_code == 200
    assert "Задание для группы 1" in res_ok.get_data(as_text=True)

    # Group 1 CANNOT view Group 2 assignment (returns 404)
    res_other = client.get(f"/student/assignments/{g2_assign_id}")
    assert res_other.status_code == 404

    # Non-existent UUID returns 404
    assert client.get(f"/student/assignments/{uuid4()}").status_code == 404


def test_two_lessons_same_topic_have_distinct_assignments(client_env):
    client, private_dir = client_env
    from tests.conftest import configure_group
    configure_group(private_dir, group="ИС 25/9-1П", password="1234", is_open=True)

    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    client.post("/admin/login", data={"password": "admin-secret"})
    r1 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-05", "pair_number": "1",
        "description": "Уникальный текст первой пары", "description_format": "txt",
    })
    r2 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-05", "pair_number": "2",
        "description": "Уникальный текст второй пары", "description_format": "txt",
    })

    client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    page_1 = client.get(f"/student/assignments/{r1.json['id']}").get_data(as_text=True)
    page_2 = client.get(f"/student/assignments/{r2.json['id']}").get_data(as_text=True)

    assert "Уникальный текст первой пары" in page_1
    assert "Уникальный текст второй пары" not in page_1

    assert "Уникальный текст второй пары" in page_2
    assert "Уникальный текст первой пары" not in page_2


def test_student_attachment_download_and_security(client_env):
    client, private_dir = client_env
    from tests.conftest import configure_group
    configure_group(private_dir, group="ИС 25/9-1П", password="1234", is_open=True)
    configure_group(private_dir, group="ИС 25/9-2П", password="5678", is_open=True)

    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    html_bytes = b"<html><body><h1>Task</h1><script>alert(1)</script></body></html>"
    client.post("/admin/login", data={"password": "admin-secret"})
    r1 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-06", "pair_number": "1",
        "attachment": (io.BytesIO(html_bytes), "task.html"),
    }, content_type="multipart/form-data")
    assign_id = r1.json["id"]

    # 1. Unauthenticated download returns 403
    assert client.get(f"/student/assignments/{assign_id}/attachment").status_code == 403

    # 2. Group 2 student returns 404
    client.post("/unlock", data={"group": "ИС 25/9-2П", "password": "5678"})
    assert client.get(f"/student/assignments/{assign_id}/attachment").status_code == 404

    # 3. Group 1 student downloads
    client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    dl_resp = client.get(f"/student/assignments/{assign_id}/attachment")
    assert dl_resp.status_code == 200
    assert dl_resp.data == html_bytes
    # Must have nosniff and attachment disposition so browser does not execute HTML as app page
    assert dl_resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert "attachment" in dl_resp.headers.get("Content-Disposition", "")

    # 4. Revoking access blocks subsequent downloads
    configure_group(private_dir, group="ИС 25/9-1П", password="different_password", is_open=False)
    assert client.get(f"/student/assignments/{assign_id}/attachment").status_code == 403
    assert client.get(f"/student/assignments/{assign_id}").status_code == 403


def test_student_can_submit_from_assignment_page(client_env, tmp_path):
    client, private_dir = client_env
    from tests.conftest import configure_group
    configure_group(private_dir, group="ИС 25/9-1П", password="1234", is_open=True)

    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    client.post("/admin/login", data={"password": "admin-secret"})
    r1 = client.post("/admin/assignments", data={
        "group_name": "ИС 25/9-1П", "subject_id": str(subject.id), "topic_id": str(topic.id),
        "lesson_date": "2026-11-07", "pair_number": "1",
    })
    assign_id = r1.json["id"]

    client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    # Verify assignment page loads form
    page = client.get(f"/student/assignments/{assign_id}")
    assert page.status_code == 200
    assert "Сдать работу" in page.get_data(as_text=True)

    # Submit work
    sub_resp = client.post("/submit", data={
        "group": "ИС 25/9-1П",
        "discipline": subject.name,
        "work": topic.name,
        "student_name": "Сидоров Петр Алексеевич",
        "file": (io.BytesIO(b"answer PDF bytes"), "homework.pdf"),
    })
    assert sub_resp.status_code == 201

    saved = list((tmp_path / "uploads").rglob("Сидоров Петр Алексеевич 1.pdf"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"answer PDF bytes"
