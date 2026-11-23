"""Tests for student submission flows, unlocking, and admin submissions review in web interface."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from flask.testing import FlaskClient

from app.access import AccessStore
from app.assignments import AssignmentStore
from app.database import initialize
from app.sqlite_catalog import SqliteCatalogStore
from app.submissions import SubmissionStore
from app.web import create_app


@pytest.fixture
def app_env(tmp_path: Path):
    db_path = tmp_path / "app_v2.sqlite3"
    initialize(db_path)
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    catalog = SqliteCatalogStore(db_path)
    group = catalog.create_group("ИС 25/9-1П")
    subject = catalog.create_subject("Информатика")
    topic = catalog.create_topic(subject.id, "Базы данных")

    access_store = AccessStore(db_path)
    access_store.configure(group.id, is_open=True, password="secret_password")

    assignments = AssignmentStore(db_path)
    assignment = assignments.create(group.id, topic.id, "2026-11-03", 1)

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-password",
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "MAX_UPLOAD_MB": 50,
        "WTF_CSRF_ENABLED": False,
    })

    return {
        "app": app,
        "client": app.test_client(),
        "db_path": db_path,
        "storage_root": storage_root,
        "group": group,
        "subject": subject,
        "topic": topic,
        "assignment": assignment,
    }


# --- Unlock & Session Tests ---

def test_unlock_failure_wrong_password(app_env):
    client: FlaskClient = app_env["client"]
    group = app_env["group"]

    res = client.post("/unlock", json={"group_id": str(group.id), "password": "wrong"})
    assert res.status_code == 403


def test_unlock_success_returns_assignments_and_students(app_env):
    client: FlaskClient = app_env["client"]
    group = app_env["group"]
    topic = app_env["topic"]

    res = client.post("/unlock", json={"group_id": str(group.id), "password": "secret_password"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["group_id"] == str(group.id)
    assert data["group_name"] == group.name
    assert len(data["assignments"]) == 1
    assert data["assignments"][0]["topic_id"] == str(topic.id)
    assert isinstance(data["students"], list)

    with client.session_transaction() as sess:
        assert sess["student_group_id"] == str(group.id)
        assert sess["student_session_schema"] == 2
        assert sess["student_access_version"] >= 2


def test_unlock_form_post_fallback_by_group_name(app_env):
    client: FlaskClient = app_env["client"]
    group = app_env["group"]

    res = client.post("/unlock", data={"group": group.name, "password": "secret_password"})
    assert res.status_code == 200
    assert res.get_json()["group_id"] == str(group.id)


# --- Student Creation & Listing ---

def test_student_creation_and_listing(app_env):
    client: FlaskClient = app_env["client"]
    group = app_env["group"]

    # Unlock first
    client.post("/unlock", json={"group_id": str(group.id), "password": "secret_password"})

    # Create student via JSON
    res = client.post("/student/students", json={"display_name": "Иванов Иван"})
    assert res.status_code == 201
    created = res.get_json()
    assert created["display_name"] == "Иванов Иван"
    student_id = created["id"]
    assert UUID(student_id)

    # Empty display name rejected
    res_bad = client.post("/student/students", json={"display_name": "   "})
    assert res_bad.status_code == 400

    # List students
    res_list = client.get("/student/students")
    assert res_list.status_code == 200
    students = res_list.get_json()
    assert len(students) == 1
    assert students[0]["id"] == student_id
    assert students[0]["display_name"] == "Иванов Иван"


def test_student_endpoints_require_session(app_env):
    client: FlaskClient = app_env["client"]
    assert client.get("/student/students").status_code == 403
    assert client.post("/student/students", json={"display_name": "Петров"}).status_code == 403
    assert client.get("/student/assignments").status_code == 403


# --- Submission Flow Tests ---

def test_submit_success_and_second_attempt(app_env):
    client: FlaskClient = app_env["client"]
    group = app_env["group"]
    assignment = app_env["assignment"]

    # 1. Unlock
    client.post("/unlock", json={"group_id": str(group.id), "password": "secret_password"})

    # 2. Add student
    st_res = client.post("/student/students", json={"display_name": "Смирнов Алексей"})
    student_id = st_res.get_json()["id"]

    # 3. Submit attempt 1
    file_content = b"%PDF-1.4 test file content attempt 1"
    res1 = client.post("/submit", data={
        "group_id": str(group.id),
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (BytesIO(file_content), "lab1.pdf"),
    })
    assert res1.status_code == 201
    sub1_data = res1.get_json()
    assert sub1_data["attempt_number"] == 1
    assert sub1_data["filename"] == "lab1.pdf"
    sub1_id = sub1_data["submission_id"]

    # 4. Submit attempt 2
    file_content2 = b"%PDF-1.4 test file content attempt 2"
    res2 = client.post("/submit", data={
        "group_id": str(group.id),
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (BytesIO(file_content2), "lab1_v2.pdf"),
    })
    assert res2.status_code == 201
    sub2_data = res2.get_json()
    assert sub2_data["attempt_number"] == 2
    assert sub2_data["submission_id"] != sub1_id


def test_submit_rejects_mismatched_group(app_env):
    client: FlaskClient = app_env["client"]
    group = app_env["group"]
    assignment = app_env["assignment"]

    client.post("/unlock", json={"group_id": str(group.id), "password": "secret_password"})
    st_res = client.post("/student/students", json={"display_name": "Тестов Тест"})
    student_id = st_res.get_json()["id"]

    # Post with another group_id in form
    other_group_id = str(UUID(int=999999))
    res = client.post("/submit", data={
        "group_id": other_group_id,
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (BytesIO(b"data"), "work.pdf"),
    })
    assert res.status_code == 403


# --- Admin Submissions, Download & Review Tests ---

def test_admin_submissions_list_download_and_review(app_env):
    client: FlaskClient = app_env["client"]
    group = app_env["group"]
    assignment = app_env["assignment"]

    # Perform a student submission first
    client.post("/unlock", json={"group_id": str(group.id), "password": "secret_password"})
    st_res = client.post("/student/students", json={"display_name": "Кузнецов Дмитрий"})
    student_id = st_res.get_json()["id"]

    sub_res = client.post("/submit", data={
        "group_id": str(group.id),
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (BytesIO(b"Report content"), "report.docx"),
    })
    submission_id = sub_res.get_json()["submission_id"]

    # 1. Admin unauthenticated access
    assert client.get("/admin/submissions").status_code == 403

    # 2. Login as admin
    login_res = client.post("/admin/login", data={"password": "admin-password"})
    assert login_res.status_code == 204

    # 3. View submissions page
    admin_page = client.get("/admin/submissions")
    assert admin_page.status_code == 200
    html = admin_page.get_data(as_text=True)
    assert "Кузнецов Дмитрий" in html
    assert "report.docx" in html
    assert "Ожидает" in html

    # 4. Download file
    dl_res = client.get(f"/admin/submissions/{submission_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.data == b"Report content"
    assert "report.docx" in dl_res.headers.get("Content-Disposition", "")

    # 5. Set review status via JSON
    rev_json = client.post(
        f"/admin/submissions/{submission_id}/review",
        json={"status": "reviewed"},
        headers={"Accept": "application/json"},
    )
    assert rev_json.status_code == 200
    rev_data = rev_json.get_json()
    assert rev_data["review_status"] == "reviewed"
    assert rev_data["reviewed_at"] is not None

    # 6. Toggle review status via Form POST (303 redirect)
    rev_form = client.post(
        f"/admin/submissions/{submission_id}/review",
        data={"status": "pending", "group_id": str(group.id)},
        headers={"Accept": "text/html"},
    )
    assert rev_form.status_code == 303
    assert f"group_id={group.id}" in rev_form.headers["Location"]


def test_admin_submissions_pagination(app_env):
    client = app_env["client"]
    group = app_env["group"]
    sub_store = SubmissionStore(app_env["db_path"], app_env["storage_root"])
    assignment = app_env["assignment"]

    # Login as admin
    client.post("/admin/login", data={"password": "admin-password"})

    access = AccessStore(app_env["db_path"]).get(group.id)
    version = access.version if access else 1

    # Create 3 submissions
    for i in range(1, 4):
        st = sub_store.create_student(group.id, f"Студент {i}")
        sub_store.submit(
            group_id=group.id,
            student_id=st.id,
            assignment_id=assignment.id,
            source=BytesIO(f"content {i}".encode()),
            original_filename=f"doc{i}.pdf",
            access_version=version,
        )

    # Page 1 with per_page=2
    res_p1 = client.get(f"/admin/submissions?page=1&per_page=2&group_id={group.id}")
    assert res_p1.status_code == 200
    html_p1 = res_p1.get_data(as_text=True)
    assert "Показано 2 из 3" in html_p1
    assert "Следующая →" in html_p1

    # Page 2 with per_page=2
    res_p2 = client.get(f"/admin/submissions?page=2&per_page=2&group_id={group.id}")
    assert res_p2.status_code == 200
    html_p2 = res_p2.get_data(as_text=True)
    assert "Показано 1 из 3" in html_p2
    assert "← Предыдущая" in html_p2
