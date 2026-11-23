"""Tests for web routes, admin panel, catalog management, security, and CSRF under SQLite v2."""

from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path

import pytest

from app.access import AccessStore
from app.assignments import AssignmentStore
from app.database import initialize
from app.sqlite_catalog import SqliteCatalogStore
from app.web import create_app
from tests.conftest import get_csrf_token, post_with_csrf


@pytest.fixture
def client_env(tmp_path: Path):
    db_path = tmp_path / "app_v2.sqlite3"
    initialize(db_path)
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    catalog = SqliteCatalogStore(db_path)
    group1 = catalog.create_group("ИС 25/9-1П")
    group2 = catalog.create_group("ИС 25/9-2П")
    subject = catalog.create_subject("Архитектура аппаратных средств")
    topic = catalog.create_topic(subject.id, "Логические элементы")

    access_store = AccessStore(db_path)
    access_store.configure(group1.id, is_open=True, password="1234")

    assignment_store = AssignmentStore(db_path)
    assignment = assignment_store.create(group1.id, topic.id, "2026-11-03", 1)

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret",
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "MAX_UPLOAD_MB": 100,
        "WTF_CSRF_ENABLED": False,
    })

    return {
        "client": app.test_client(),
        "db_path": db_path,
        "storage_root": storage_root,
        "group1": group1,
        "group2": group2,
        "subject": subject,
        "topic": topic,
        "assignment": assignment,
    }


@pytest.fixture
def client_csrf_env(tmp_path: Path):
    db_path = tmp_path / "app_v2.sqlite3"
    initialize(db_path)
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    catalog = SqliteCatalogStore(db_path)
    group1 = catalog.create_group("ИС 25/9-1П")
    group2 = catalog.create_group("ИС 25/9-2П")
    subject = catalog.create_subject("Архитектура аппаратных средств")
    topic = catalog.create_topic(subject.id, "Логические элементы")

    access_store = AccessStore(db_path)
    access_store.configure(group1.id, is_open=True, password="1234")

    assignment_store = AssignmentStore(db_path)
    assignment = assignment_store.create(group1.id, topic.id, "2026-11-03", 1)

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret",
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "MAX_UPLOAD_MB": 100,
        "WTF_CSRF_ENABLED": True,
    })

    return {
        "client": app.test_client(),
        "db_path": db_path,
        "storage_root": storage_root,
        "group1": group1,
        "group2": group2,
        "subject": subject,
        "topic": topic,
        "assignment": assignment,
    }


def test_index_page_renders_catalog(client_env):
    client = client_env["client"]
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "ИС 25/9-1П" in html
    assert "Архитектура аппаратных средств" in html


def test_student_submit_requires_group_authorization(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]
    assignment = client_env["assignment"]

    res = client.post("/submit", data={
        "group_id": str(group1.id),
        "student_id": "00000000-0000-0000-0000-000000000001",
        "assignment_id": str(assignment.id),
        "file": (BytesIO(b"content"), "homework.pdf"),
    })
    assert res.status_code == 403


def test_student_can_upload_only_after_unlocking(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]
    assignment = client_env["assignment"]

    unlock_res = client.post("/unlock", json={"group_id": str(group1.id), "password": "1234"})
    assert unlock_res.status_code == 200

    # Create a student in group1
    st_res = client.post("/student/students", json={"display_name": "Иванов Иван"})
    assert st_res.status_code == 201
    student_id = st_res.json["id"]

    # Submit homework
    res = client.post("/submit", data={
        "group_id": str(group1.id),
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (BytesIO(b"answer 1"), "answer.pdf"),
    })
    assert res.status_code == 201
    assert res.json["attempt_number"] == 1


def test_upload_denied_when_session_group_differs(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]
    group2 = client_env["group2"]
    assignment = client_env["assignment"]

    client.post("/unlock", json={"group_id": str(group1.id), "password": "1234"})
    st_res = client.post("/student/students", json={"display_name": "Иванов Иван"})
    student_id = st_res.json["id"]

    # Attempt to submit with group2 in form while unlocked for group1
    res = client.post("/submit", data={
        "group_id": str(group2.id),
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (BytesIO(b"content"), "file.pdf"),
    })
    assert res.status_code == 403


def test_reconfiguring_group_invalidates_unlocked_student(client_env):
    client = client_env["client"]
    db_path = client_env["db_path"]
    group1 = client_env["group1"]
    assignment = client_env["assignment"]

    client.post("/unlock", json={"group_id": str(group1.id), "password": "1234"})
    st_res = client.post("/student/students", json={"display_name": "Иванов Иван"})
    student_id = st_res.json["id"]

    # Teacher changes password -> bumps access version
    access_store = AccessStore(db_path)
    access_store.configure(group1.id, is_open=True, password="new-password")

    # Subsequent submission attempt rejected due to version mismatch
    res = client.post("/submit", data={
        "group_id": str(group1.id),
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (BytesIO(b"content"), "file.pdf"),
    })
    assert res.status_code == 403


def test_admin_login_lifecycle(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]

    assert client.post("/admin/access", data={"group_id": str(group1.id), "is_open": "1"}).status_code == 403
    assert client.post("/admin/login", data={"password": "wrong"}).status_code == 403
    assert client.post("/admin/login", data={"password": "admin-secret"}).status_code == 204

    # Now authorized
    assert client.get("/admin/access").status_code == 200

    # Logout
    client.post("/admin/logout")
    assert client.post("/admin/access", data={"group_id": str(group1.id), "is_open": "1"}).status_code == 403


def test_admin_update_group_access(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]

    client.post("/admin/login", data={"password": "admin-secret"})

    # Update group1 access password
    res = client.post("/admin/access", data={
        "group_id": str(group1.id),
        "is_open": "1",
        "password": "group-password",
        "closes_at": "",
    })
    assert res.status_code in (200, 204)

    # Student can unlock with new password
    unlock_res = client.post("/unlock", json={"group_id": str(group1.id), "password": "group-password"})
    assert unlock_res.status_code == 200


def test_admin_setting_past_deadline_blocks_unlock(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]

    client.post("/admin/login", data={"password": "admin-secret"})
    client.post("/admin/access", data={
        "group_id": str(group1.id),
        "is_open": "1",
        "password": "group-password",
        "closes_at": "2020-01-01T00:00",
    })

    assert client.post("/unlock", json={"group_id": str(group1.id), "password": "group-password"}).status_code == 403


@pytest.mark.parametrize("kind", ["groups", "subjects"])
def test_admin_catalog_crud_and_archive(client_env, kind):
    client = client_env["client"]

    assert client.post(f"/admin/{kind}", data={"name": "Тест"}).status_code == 403

    client.post("/admin/login", data={"password": "admin-secret"})

    # Create
    created = client.post(f"/admin/{kind}", data={"name": "Новый элемент"})
    assert created.status_code == 201
    item_id = created.json["id"]

    # Duplicate name conflict
    dup = client.post(f"/admin/{kind}", data={"name": "новый элемент"})
    assert dup.status_code == 400
    assert dup.json["error"] == "name_conflict"

    # Rename
    renamed = client.post(f"/admin/{kind}/{item_id}/rename", data={"name": "Переименовано"})
    assert renamed.status_code == 200
    assert renamed.json["name"] == "Переименовано"

    # Archive
    archive_res = client.post(f"/admin/{kind}/{item_id}/archive")
    assert archive_res.status_code == 204

    # Delete route alias also works
    del_res = client.post(f"/admin/{kind}/{item_id}/delete")
    assert del_res.status_code in (204, 404)


def test_admin_topics_crud_move_and_archive(client_env):
    client = client_env["client"]
    subject = client_env["subject"]

    client.post("/admin/login", data={"password": "admin-secret"})

    # Create topic
    t_res = client.post("/admin/topics", data={"name": "Новая тема", "subject_id": str(subject.id)})
    assert t_res.status_code == 201
    topic_id = t_res.json["id"]

    # Duplicate in same subject fails
    dup = client.post("/admin/topics", data={"name": "новая тема", "subject_id": str(subject.id)})
    assert dup.status_code == 400
    assert dup.json["error"] == "topic_name_conflict"

    # Rename topic
    ren = client.post(f"/admin/topics/{topic_id}/rename", data={"name": "Тема изменена"})
    assert ren.status_code == 200
    assert ren.json["name"] == "Тема изменена"

    # Move topics
    subj2_res = client.post("/admin/subjects", data={"name": "Второй предмет"})
    subj2_id = subj2_res.json["id"]

    move_res = client.post("/admin/topics/move", data={
        "topic_ids": [topic_id],
        "subject_id": subj2_id,
    })
    assert move_res.status_code in (200, 204)

    # Archive topic
    arch = client.post(f"/admin/topics/{topic_id}/archive")
    assert arch.status_code == 204


def test_system_subject_protected_from_rename_and_archive(client_env):
    client = client_env["client"]
    db_path = client_env["db_path"]

    catalog = SqliteCatalogStore(db_path)
    sys_subject = next(s for s in catalog.list_subjects() if s.name == "Без предмета")

    client.post("/admin/login", data={"password": "admin-secret"})

    ren = client.post(f"/admin/subjects/{sys_subject.id}/rename", data={"name": "Hacked"})
    assert ren.status_code == 400

    arch = client.post(f"/admin/subjects/{sys_subject.id}/archive")
    assert arch.status_code == 400


def test_admin_assignments_crud(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]
    topic = client_env["topic"]

    client.post("/admin/login", data={"password": "admin-secret"})

    # Create assignment
    res = client.post("/admin/assignments", data={
        "group_id": str(group1.id),
        "topic_id": str(topic.id),
        "lesson_date": "2026-12-01",
        "pair_number": "2",
    })
    assert res.status_code == 201
    assignment_id = res.json["id"]

    # Slot conflict
    conflict = client.post("/admin/assignments", data={
        "group_id": str(group1.id),
        "topic_id": str(topic.id),
        "lesson_date": "2026-12-01",
        "pair_number": "2",
    })
    assert conflict.status_code == 409
    assert conflict.json["error"] in ("assignment_conflict", "slot_conflict")

    # Update assignment
    upd = client.post(f"/admin/assignments/{assignment_id}/update", data={
        "topic_id": str(topic.id),
        "lesson_date": "2026-12-02",
        "pair_number": "3",
    })
    assert upd.status_code == 200

    # Archive assignment
    arch = client.post(f"/admin/assignments/{assignment_id}/archive")
    assert arch.status_code == 204


def test_upload_file_too_large(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]
    assignment = client_env["assignment"]

    client.post("/unlock", json={"group_id": str(group1.id), "password": "1234"})
    st_res = client.post("/student/students", json={"display_name": "Большой Файл"})
    student_id = st_res.json["id"]

    # Exceed MAX_UPLOAD_MB (configured to 100 MB in fixture, we send 105 MB)
    large_stream = BytesIO(b"0" * (105 * 1024 * 1024))
    res = client.post("/submit", data={
        "group_id": str(group1.id),
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (large_stream, "large.pdf"),
    })
    assert res.status_code == 413


# ==============================================================================
# Security & Upload Validation Tests
# ==============================================================================


def test_upload_mb_configuration_and_frontend_limit(tmp_path: Path):
    db_path = tmp_path / "app_v2.sqlite3"
    initialize(db_path)
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": tmp_path / "uploads",
        "MAX_UPLOAD_MB": 250,
    })
    assert app.config["MAX_CONTENT_LENGTH"] == 250 * 1024 * 1024
    assert app.config["LIMIT_LABEL"] == "250 МБ"

    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert "Максимальный размер — 250 МБ." in html
    assert "window.submissionMaxBytes = 262144000;" in html
    assert "window.submissionLimit = " in html
    assert "250" in html


def test_upload_gb_configuration_and_frontend_limit(tmp_path: Path):
    db_path = tmp_path / "app_v2.sqlite3"
    initialize(db_path)
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": tmp_path / "uploads",
        "MAX_UPLOAD_GB": 2,
    })
    assert app.config["MAX_CONTENT_LENGTH"] == 2 * 1024 * 1024 * 1024
    assert app.config["LIMIT_LABEL"] == "2 ГБ"

    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert "Максимальный размер — 2 ГБ." in html
    assert "window.submissionMaxBytes = 2147483648;" in html
    assert "window.submissionLimit = " in html
    assert "2" in html


@pytest.mark.parametrize("invalid_key", [
    None,
    "",
    "   \t\n",
    "dev-secret",
    "  dev-secret  ",
    "DEV-SECRET",
    "replace-with-a-long-random-secret",
    "  REPLACE-WITH-A-LONG-RANDOM-SECRET  ",
    "short-secret-under-32-bytes",
    1234567890123456789012345678901234,
    b"dev-secret",
    b"short-bytes",
    b"replace-with-a-long-random-secret",
])
def test_secret_key_validation_invalid_values_fail_closed(tmp_path: Path, invalid_key, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    private_dir = tmp_path / "never_created_private"
    storage_dir = tmp_path / "never_created_storage"
    cfg = {
        "TESTING": True,
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": storage_dir,
    }
    if invalid_key is not None:
        cfg["SECRET_KEY"] = invalid_key

    with pytest.raises((RuntimeError, TypeError)) as exc_info:
        create_app(cfg)

    msg = str(exc_info.value)
    assert msg == "SECRET_KEY must be a non-placeholder secret of at least 32 bytes"
    if isinstance(invalid_key, (str, bytes)) and invalid_key:
        key_str = invalid_key.decode() if isinstance(invalid_key, bytes) else invalid_key
        assert key_str not in msg
    assert not private_dir.exists()
    assert not storage_dir.exists()


def test_secret_key_validation_valid_formats(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "db.sqlite3"
    initialize(db_path)
    valid_str = "a" * 32
    app1 = create_app({
        "TESTING": True,
        "SECRET_KEY": valid_str,
        "DATABASE_PATH": db_path,
        "PRIVATE_DATA_DIR": tmp_path / "p1",
    })
    assert app1.config["SECRET_KEY"] == valid_str

    valid_bytes = b"b" * 32
    app2 = create_app({
        "TESTING": True,
        "SECRET_KEY": valid_bytes,
        "DATABASE_PATH": db_path,
        "PRIVATE_DATA_DIR": tmp_path / "p2",
    })
    assert app2.config["SECRET_KEY"] == valid_bytes

    monkeypatch.setenv("SECRET_KEY", "c" * 32)
    app3 = create_app({
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "PRIVATE_DATA_DIR": tmp_path / "p3",
    })
    assert app3.config["SECRET_KEY"] == "c" * 32


def test_session_cookie_contains_httponly_and_samesite_lax(client_csrf_env):
    client = client_csrf_env["client"]
    res = post_with_csrf(client, "/admin/login", csrf_page="/admin", data={"password": "admin-secret"})
    assert res.status_code == 204
    cookies = res.headers.getlist("Set-Cookie")
    session_cookie = next((c for c in cookies if c.startswith("session=")), None)
    assert session_cookie is not None
    assert "HttpOnly" in session_cookie
    assert "SameSite=Lax" in session_cookie


def test_submit_disallowed_extension_rejected(client_env):
    client = client_env["client"]
    group1 = client_env["group1"]
    assignment = client_env["assignment"]

    client.post("/unlock", json={"group_id": str(group1.id), "password": "1234"})
    st_res = client.post("/student/students", json={"display_name": "Тестер"})
    student_id = st_res.json["id"]

    res = client.post("/submit", data={
        "group_id": str(group1.id),
        "student_id": student_id,
        "assignment_id": str(assignment.id),
        "file": (BytesIO(b"binary"), "malicious.exe"),
    })
    assert res.status_code == 400
    assert res.json["error"] == "file_type_not_allowed"


# ==============================================================================
# CSRF Protection Tests
# ==============================================================================


def test_csrf_token_present_in_html_meta_and_forms(client_csrf_env):
    client = client_csrf_env["client"]
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert '<meta name="csrf-token"' in html
    assert 'name="csrf_token"' in html


def test_csrf_form_token_vs_header_token_both_pass(client_csrf_env):
    client = client_csrf_env["client"]
    group1 = client_csrf_env["group1"]

    # 1. Form token
    token1 = get_csrf_token(client, "/")
    res_form = client.post("/unlock", data={
        "group_id": str(group1.id),
        "password": "1234",
        "csrf_token": token1,
    })
    assert res_form.status_code in (200, 204)

    # 2. Header token
    client2 = client.application.test_client()
    token2 = get_csrf_token(client2, "/")
    res_hdr = client2.post(
        "/unlock",
        data={"group_id": str(group1.id), "password": "1234"},
        headers={"X-CSRFToken": token2},
    )
    assert res_hdr.status_code in (200, 204)


def test_csrf_login_and_unlock_before_authorization(client_csrf_env):
    client = client_csrf_env["client"]
    group1 = client_csrf_env["group1"]

    token_unlock = get_csrf_token(client, "/")
    unlock_res = client.post(
        "/unlock",
        data={"group_id": str(group1.id), "password": "1234"},
        headers={"X-CSRFToken": token_unlock},
    )
    assert unlock_res.status_code in (200, 204)

    token_login = get_csrf_token(client, "/admin")
    login_res = client.post(
        "/admin/login",
        data={"password": "admin-secret"},
        headers={"X-CSRFToken": token_login},
    )
    assert login_res.status_code == 204


def test_csrf_logout_without_token_does_not_log_out_and_with_token_logs_out(client_csrf_env):
    client = client_csrf_env["client"]
    post_with_csrf(client, "/admin/login", csrf_page="/admin", data={"password": "admin-secret"})
    with client.session_transaction() as sess:
        assert sess.get("admin") is True

    # Logout without CSRF token fails (400) and session remains
    res_missing = client.post("/admin/logout", headers={"Accept": "application/json"})
    assert res_missing.status_code == 400
    assert res_missing.json["error"] == "csrf_error"
    with client.session_transaction() as sess:
        assert sess.get("admin") is True

    # Logout with valid CSRF token succeeds
    res_valid = post_with_csrf(client, "/admin/logout")
    assert res_valid.status_code in (200, 302)
    with client.session_transaction() as sess:
        assert "admin" not in sess


def test_csrf_authenticated_admin_cookie_without_csrf_token_cannot_mutate(client_csrf_env):
    client = client_csrf_env["client"]
    group1 = client_csrf_env["group1"]
    post_with_csrf(client, "/admin/login", csrf_page="/admin", data={"password": "admin-secret"})
    with client.session_transaction() as sess:
        assert sess.get("admin") is True

    headers = {"Accept": "application/json"}
    assert client.post("/admin/groups", data={"name": "Attacked"}, headers=headers).status_code == 400
    assert client.post("/admin/access", data={"group_id": str(group1.id), "is_open": "0"}, headers=headers).status_code == 400
    assert client.post("/admin/logout", headers=headers).status_code == 400


def test_csrf_expired_token_rejected_deterministically(client_csrf_env, monkeypatch):
    client = client_csrf_env["client"]
    group1 = client_csrf_env["group1"]

    t0 = 1700000000.0
    monkeypatch.setattr(time, "time", lambda: t0)
    token = get_csrf_token(client, "/")

    # Advance time beyond WTF_CSRF_TIME_LIMIT (3600 seconds)
    monkeypatch.setattr(time, "time", lambda: t0 + 4000.0)
    res_expired = client.post(
        "/unlock",
        data={"group_id": str(group1.id), "password": "1234"},
        headers={"X-CSRFToken": token, "Accept": "application/json"},
    )
    assert res_expired.status_code == 400
    assert res_expired.is_json
    assert res_expired.json["error"] == "csrf_error"
