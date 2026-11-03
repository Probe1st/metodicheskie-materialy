from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Event
import time

import pytest
from flask import Flask
from app.access import AccessStore
from app.assignments import AssignmentStore
from app.catalog_store import create_catalog_store
from app.web import create_app
from tests.conftest import get_csrf_token, post_with_csrf


@pytest.fixture
def client_env(tmp_path: Path):
    private_dir = tmp_path / ".private"
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": catalog_path,
        "MAX_UPLOAD_GB": 1,
    })
    return app.test_client(), private_dir


def valid_submission():
    return {
        "group": "ИС 25/9-1П",
        "discipline": "Архитектура аппаратных средств",
        "work": "Логические элементы. Базовые схемы",
        "student_name": "Иванов Иван Иванович",
        "file": (BytesIO(b"answer"), "answer.pdf"),
    }


def configure_group(private_dir: Path, group="ИС 25/9-1П", password="1234", is_open=True):
    store = AccessStore(private_dir / "access.sqlite3", ["ИС 25/9-1П", "ИС 25/9-2П"])
    store.configure(group, password, is_open, None)


def test_submit_requires_group_authorization(client_env):
    client, _ = client_env
    assert client.post("/submit", data=valid_submission()).status_code == 400
    assert post_with_csrf(client, "/submit", data=valid_submission()).status_code == 403

def test_student_can_upload_only_after_unlocking_the_selected_open_group(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)

    unlock_response = post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert unlock_response.status_code == 204

    response = post_with_csrf(client, "/submit", data=valid_submission())
    assert response.status_code == 201
    assert list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))


def test_upload_is_denied_when_the_student_session_group_differs(client_env):
    client, private_dir = client_env
    configure_group(private_dir)

    post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    payload = valid_submission()
    payload["group"] = "ИС 25/9-2П"

    assert post_with_csrf(client, "/submit", data=payload).status_code == 403

def test_reconfiguring_group_invalidates_unlocked_student(client_env):
    client, private_dir = client_env
    configure_group(private_dir)

    post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    configure_group(private_dir, password="5678", is_open=False)

    assert post_with_csrf(client, "/submit", data=valid_submission()).status_code == 403

def test_admin_login_is_required_to_access_and_change_groups(client_env):
    client, _ = client_env
    assert client.post("/admin/access", data={"group": "ИС 25/9-1П", "is_open": "1"}).status_code == 400
    assert post_with_csrf(client, "/admin/access", data={"group": "ИС 25/9-1П", "is_open": "1"}).status_code == 403
    assert post_with_csrf(client, "/admin/login", data={"password": "wrong"}).status_code == 403
    assert post_with_csrf(client, "/admin/login", data={"password": "admin-secret"}).status_code == 204

def test_admin_can_open_group_and_student_can_unlock_and_submit(client_env, tmp_path):
    client, _ = client_env
    assert post_with_csrf(client, "/admin/login", data={"password": "admin-secret"}).status_code == 204

    save_response = post_with_csrf(client, "/admin/access", data={
        "group": "ИС 25/9-1П",
        "password": "group-password",
        "is_open": "1",
        "closes_at": "",
    })
    assert save_response.status_code == 204

    unlock_response = post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "group-password"})
    assert unlock_response.status_code == 204

    submit_response = post_with_csrf(client, "/submit", data=valid_submission())
    assert submit_response.status_code == 201
    assert list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))

def test_admin_page_shows_all_catalog_groups(client_env):
    client, _ = client_env
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    page = client.get("/admin/access").get_data(as_text=True)

    assert "ИС 25/9-1П" in page
    assert "ИС 25/9-2П" in page


def test_admin_setting_past_deadline_immediately_blocks_submission(client_env):
    client, _ = client_env
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    post_with_csrf(client, "/admin/access", data={
        "group": "ИС 25/9-1П",
        "password": "group-password",
        "is_open": "1",
        "closes_at": "2020-01-01T00:00",
    })

    assert post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "group-password"}).status_code == 403

@pytest.mark.parametrize("kind", ("groups", "subjects"))
def test_admin_can_create_rename_and_delete_catalog_item(client_env, kind):
    client, _ = client_env
    assert client.get(f"/admin/{kind}").status_code == 403
    assert post_with_csrf(client, f"/admin/{kind}", data={"name": "Тест"}).status_code == 403
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    created = post_with_csrf(client, f"/admin/{kind}", data={"name": "Тест"})
    assert created.status_code == 201
    uid = created.json["id"]
    post_with_csrf(client, "/admin/logout")
    assert post_with_csrf(client, f"/admin/{kind}/{uid}/rename", data={"name": "Чужое"}).status_code == 403
    assert post_with_csrf(client, f"/admin/{kind}/{uid}/delete").status_code == 403
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    duplicate = post_with_csrf(client, f"/admin/{kind}", data={"name": "тест"})
    assert duplicate.status_code == 400
    assert duplicate.json["error"] == "name_conflict"
    assert post_with_csrf(client, f"/admin/{kind}/{uid}/rename", data={"name": "Переименовано"}).status_code == 200
    assert "Переименовано" in client.get(f"/admin/{kind}").get_data(as_text=True)
    assert post_with_csrf(client, f"/admin/{kind}/{uid}/delete").status_code == 204
    assert post_with_csrf(client, f"/admin/{kind}/{uid}/delete").status_code == 404

def test_admin_topics_require_subject_and_support_single_and_bulk_mapping(client_env):
    client, _ = client_env
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    source, target = store.list_subjects()[:2]
    assert post_with_csrf(client, "/admin/topics", data={"name": "Новая тема", "subject_id": str(source.id)}).status_code == 403
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    assert post_with_csrf(client, "/admin/topics", data={"name": "Без владельца"}).status_code == 400
    created = post_with_csrf(client, "/admin/topics", data={"name": "Новая тема", "subject_id": str(source.id)})
    assert created.status_code == 201
    uid = created.json["id"]
    assert created.json["subject_id"] == str(source.id)
    assert post_with_csrf(client, "/admin/topics", data={"name": "новая ТЕМА", "subject_id": str(source.id)}).status_code == 400
    assert post_with_csrf(client, "/admin/topics", data={"name": "Новая тема", "subject_id": str(target.id)}).status_code == 201
    updated = post_with_csrf(client, f"/admin/topics/{uid}/update", data={
        "name": "Другое название", "subject_id": str(target.id),
    })
    assert updated.status_code == 200
    assert updated.json["subject_id"] == str(target.id)
    post_with_csrf(client, "/admin/logout")
    assert post_with_csrf(client, "/admin/topics/move", data={
        "topic_ids": [uid], "subject_id": str(source.id),
    }).status_code == 403
    assert post_with_csrf(client, f"/admin/topics/{uid}/update", data={
        "name": "Чужое", "subject_id": str(source.id),
    }).status_code == 403
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    moved = post_with_csrf(client, "/admin/topics/move", data={
        "topic_ids": [uid], "subject_id": str(source.id),
    })
    assert moved.status_code == 200
    assert moved.json["topics"][0]["subject_id"] == str(source.id)
    assert post_with_csrf(client, f"/admin/topics/{uid}/delete").status_code == 204


def test_bulk_topic_conflict_and_subject_delete_keep_catalog_unchanged(client_env):
    client, _ = client_env
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    first, second = store.list_subjects()[:2]
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    left = post_with_csrf(client, "/admin/topics", data={"name": "Общий заголовок", "subject_id": str(first.id)}).json["id"]
    right = post_with_csrf(client, "/admin/topics", data={"name": "Общий заголовок", "subject_id": str(second.id)}).json["id"]
    before = store.snapshot()
    conflict = post_with_csrf(client, "/admin/topics/move", data={
        "topic_ids": [left, right], "subject_id": str(store.snapshot().unassigned_subject_id),
    })
    assert conflict.status_code == 400
    assert conflict.json["error"] == "topic_name_conflict"
    assert "Общий заголовок" in conflict.json["names"]
    assert store.snapshot() == before
    other = post_with_csrf(client, "/admin/topics", data={
        "name": "Логические элементы. Базовые схемы", "subject_id": str(store.snapshot().unassigned_subject_id),
    })
    assert other.status_code == 201
    before = store.snapshot()
    conflict = post_with_csrf(client, f"/admin/subjects/{first.id}/delete")
    assert conflict.status_code == 400
    assert conflict.json["error"] == "topic_name_conflict"
    assert store.snapshot() == before
    post_with_csrf(client, f"/admin/topics/{other.json['id']}/delete")
    assert post_with_csrf(client, f"/admin/subjects/{first.id}/delete").status_code == 204
    assert all(topic.subject_id != first.id for topic in store.list_topics())
    assert post_with_csrf(client, f"/admin/subjects/{store.snapshot().unassigned_subject_id}/delete").status_code == 400


def test_deleted_group_removes_access_and_files_survive_recreation(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    assert post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 204
    assert post_with_csrf(client, "/submit", data=valid_submission()).status_code == 201
    saved_files = list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))
    assert len(saved_files) == 1
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    uid = next(item.id for item in store.list_groups() if item.name == "ИС 25/9-1П")
    assert post_with_csrf(client, f"/admin/groups/{uid}/delete").status_code == 204
    assert "ИС 25/9-1П" not in {item.group for item in AccessStore(private_dir / "access.sqlite3", []).groups()}
    assert post_with_csrf(client, "/admin/groups", data={"name": "ИС 25/9-1П"}).status_code == 201
    assert post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 403
    assert post_with_csrf(client, "/submit", data=valid_submission()).status_code == 403
    assert saved_files[0].read_bytes() == b"answer"


def test_renaming_group_preserves_password_and_revokes_student_session(client_env):
    client, private_dir = client_env
    configure_group(private_dir)
    assert post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 204
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    uid = next(item.id for item in store.list_groups() if item.name == "ИС 25/9-1П")
    response = post_with_csrf(client, f"/admin/groups/{uid}/rename", data={"name": "Группа П"})
    assert response.status_code == 200
    assert post_with_csrf(client, "/submit", data=valid_submission()).status_code == 403
    assert post_with_csrf(client, "/unlock", data={"group": "Группа П", "password": "1234"}).status_code == 204
    submission = valid_submission()
    submission["group"] = "Группа П"
    assert post_with_csrf(client, "/submit", data=submission).status_code == 201

def test_group_rename_keeps_access_when_access_page_reads_concurrently(client_env, monkeypatch):
    client, private_dir = client_env
    configure_group(private_dir)
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    other = client.application.test_client()
    post_with_csrf(other, "/admin/login", data={"password": "admin-secret"})
    from app.catalog_store import create_catalog_store
    uid = next(item.id for item in create_catalog_store(client.application.config).list_groups()
               if item.name == "ИС 25/9-1П")
    original = AccessStore.rename_group
    started = Event()
    reader_started = Event()
    reader_done = Event()

    def rename_while_reading(self, old, new):
        started.set()
        assert reader_started.wait(2)
        reader_done.wait(0.5)
        return original(self, old, new)

    monkeypatch.setattr(AccessStore, "rename_group", rename_while_reading)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def read_access():
            assert started.wait(2)
            reader_started.set()
            try:
                return other.get("/admin/access").status_code
            finally:
                reader_done.set()

        result = pool.submit(read_access)
        response = post_with_csrf(client, f"/admin/groups/{uid}/rename", data={"name": "Новая группа"})
        assert result.result(timeout=5) == 200
    assert response.status_code == 200
    assert post_with_csrf(client, "/unlock", data={"group": "Новая группа", "password": "1234"}).status_code == 204


def test_unlock_cannot_bind_old_password_to_recreated_group(client_env, monkeypatch):
    student, private_dir = client_env
    configure_group(private_dir)
    admin = student.application.test_client()
    post_with_csrf(admin, "/admin/login", data={"password": "admin-secret"})
    from app.catalog_store import create_catalog_store
    uid = next(item.id for item in create_catalog_store(student.application.config).list_groups()
               if item.name == "ИС 25/9-1П")
    original = AccessStore.authorize
    authenticated = Event()
    admin_started = Event()
    admin_done = Event()

    def authorize_during_recreation(self, group, password, now=None):
        version = original(self, group, password, now)
        authenticated.set()
        assert admin_started.wait(2)
        admin_done.wait(0.5)
        return version

    monkeypatch.setattr(AccessStore, "authorize", authorize_during_recreation)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def recreate():
            assert authenticated.wait(2)
            admin_started.set()
            try:
                assert post_with_csrf(admin, f"/admin/groups/{uid}/delete").status_code == 204
                assert post_with_csrf(admin, "/admin/groups", data={"name": "ИС 25/9-1П"}).status_code == 201
                assert post_with_csrf(admin, "/admin/access", data={
                    "group": "ИС 25/9-1П", "password": "different", "is_open": "1",
                }).status_code == 204
            finally:
                admin_done.set()

        result = pool.submit(recreate)
        assert post_with_csrf(student, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 204
        result.result(timeout=5)
    assert post_with_csrf(student, "/submit", data=valid_submission()).status_code == 403


def test_deleted_topic_blocks_new_submission_without_deleting_previous_upload(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert post_with_csrf(client, "/submit", data=valid_submission()).status_code == 201
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    uid = next(item.id for item in store.list_topics() if item.name == "Логические элементы. Базовые схемы")
    assert post_with_csrf(client, f"/admin/topics/{uid}/delete").status_code == 204
    assert post_with_csrf(client, "/submit", data=valid_submission()).status_code == 400
    assert list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))


def test_new_subject_and_topic_require_matching_relationship(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    other_subject = store.list_subjects()[0]
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    subject = post_with_csrf(client, "/admin/subjects", data={"name": "Новый предмет"}).json
    topic = post_with_csrf(client, "/admin/topics", data={
        "name": "Новая тема", "subject_id": subject["id"],
    }).json
    post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})

    submission = valid_submission()
    submission["discipline"] = subject["name"]
    submission["work"] = topic["name"]
    assert post_with_csrf(client, "/submit", data=submission).status_code == 201
    original_path = next((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))

    assert post_with_csrf(client, f"/admin/topics/{topic['id']}/update", data={
        "name": topic["name"], "subject_id": str(other_subject.id),
    }).status_code == 200
    mismatched_submission = valid_submission()
    mismatched_submission["discipline"] = subject["name"]
    mismatched_submission["work"] = topic["name"]
    assert post_with_csrf(client, "/submit", data=mismatched_submission).status_code == 400
    assert original_path.exists()

    matching_submission = valid_submission()
    matching_submission["discipline"] = other_subject.name
    matching_submission["work"] = topic["name"]
    assert post_with_csrf(client, "/submit", data=matching_submission).status_code == 201
    assert original_path.exists()


def test_student_unlock_standard_and_json(client_env):
    client, private_dir = client_env
    configure_group(private_dir)
    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    store = AssignmentStore(private_dir / "access.sqlite3")
    store.create("ИС 25/9-1П", subject.id, topic.id, "2026-10-14", 2)

    # Standard form post without Accept: application/json gives 204
    resp_204 = post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert resp_204.status_code == 204

    # Post with Accept: application/json gives 200 with assignments payload
    resp_json = post_with_csrf(
        client,
        "/unlock",
        data={"group": "ИС 25/9-1П", "password": "1234"},
        headers={"Accept": "application/json"},
    )
    assert resp_json.status_code == 200
    data = resp_json.json
    assert data["status"] == "ok"
    assert data["group"] == "ИС 25/9-1П"
    assert len(data["assignments"]) == 1
    assignment = data["assignments"][0]
    assert assignment["topic_name"] == topic.name
    assert assignment["pair_number"] == 2
    assert assignment["lesson_date"] == "2026-10-14"
    assert "14.10, пара 2 — " in assignment["label"]


def test_student_assignments_requires_unlock_and_returns_list(client_env):
    client, private_dir = client_env
    configure_group(private_dir)
    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    store = AssignmentStore(private_dir / "access.sqlite3")
    created = store.create("ИС 25/9-1П", subject.id, topic.id, "2026-10-14", 2)

    # Without unlock: 403
    unauth_resp = client.get("/student/assignments")
    assert unauth_resp.status_code == 403

    # Unlock group
    unlock_resp = post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert unlock_resp.status_code == 204

    # With unlock: 200 with assignments
    resp = client.get("/student/assignments")
    assert resp.status_code == 200
    data = resp.json
    assert data["status"] == "ok"
    assert data["group"] == "ИС 25/9-1П"
    assert len(data["assignments"]) == 1
    assert data["assignments"][0]["id"] == str(created.id)
    assert data["assignments"][0]["pair_number"] == 2


def test_admin_assignments_requires_admin_auth(client_env):
    client, _ = client_env
    assert client.get("/admin/assignments").status_code == 403
    assert client.post("/admin/assignments", data={}).status_code == 400
    assert client.post("/admin/assignments/11111111-1111-1111-1111-111111111111/update", data={}).status_code == 400
    assert client.post("/admin/assignments/11111111-1111-1111-1111-111111111111/delete").status_code == 400
    assert post_with_csrf(client, "/admin/assignments", data={}).status_code == 403
    assert post_with_csrf(client, "/admin/assignments/11111111-1111-1111-1111-111111111111/update", data={}).status_code == 403
    assert post_with_csrf(client, "/admin/assignments/11111111-1111-1111-1111-111111111111/delete").status_code == 403


def test_admin_assignments_crud_and_conflict(client_env):
    from uuid import UUID

    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    client, private_dir = client_env
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})

    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # 1. Create assignment (201)
    create_resp = post_with_csrf(
        client,
        "/admin/assignments",
        data={
            "group_name": "ИС 25/9-1П",
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-14",
            "pair_number": "2",
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json["status"] == "created"
    created_id = create_resp.json["id"]

    # 2. Duplicate pair conflict (409)
    conflict_resp = post_with_csrf(
        client,
        "/admin/assignments",
        data={
            "group_name": "ИС 25/9-1П",
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-14",
            "pair_number": "2",
        },
    )
    assert conflict_resp.status_code == 409
    assert conflict_resp.json["error"] == "assignment_conflict"

    # Validation error (400)
    bad_resp = post_with_csrf(
        client,
        "/admin/assignments",
        data={
            "group_name": "ИС 25/9-1П",
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "not-a-date",
            "pair_number": "2",
        },
    )
    assert bad_resp.status_code == 400

    # 3. Create second assignment on pair 1
    create2_resp = post_with_csrf(
        client,
        "/admin/assignments",
        data={
            "group_name": "ИС 25/9-1П",
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-14",
            "pair_number": "1",
        },
    )
    assert create2_resp.status_code == 201
    id2 = create2_resp.json["id"]

    # 4. Update assignment 1 to pair 3 (200)
    update_resp = post_with_csrf(
        client,
        f"/admin/assignments/{created_id}/update",
        data={
            "lesson_date": "2026-10-14",
            "pair_number": "3",
            "topic_id": str(topic.id),
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json["pair_number"] == 3

    # 5. Update assignment 1 to taken pair 1 conflicts (409)
    conflict_update = post_with_csrf(
        client,
        f"/admin/assignments/{created_id}/update",
        data={
            "lesson_date": "2026-10-14",
            "pair_number": "1",
            "topic_id": str(topic.id),
        },
    )
    assert conflict_update.status_code == 409
    assert conflict_update.json["error"] == "assignment_conflict"

    # 6. Delete assignment (204)
    del_resp = post_with_csrf(client, f"/admin/assignments/{created_id}/delete")
    assert del_resp.status_code == 204

    store = AssignmentStore(private_dir / "access.sqlite3")
    assert store.get(UUID(created_id)) is None
    assert store.get(UUID(id2)) is not None


def test_group_rename_and_delete_cascades_to_assignments(client_env):
    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    client, private_dir = client_env
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})

    catalog = create_catalog_store(client.application.config).snapshot()
    group = next(g for g in catalog.groups if g.name == "ИС 25/9-1П")
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    store = AssignmentStore(private_dir / "access.sqlite3")
    store.create("ИС 25/9-1П", subject.id, topic.id, "2026-10-14", 1)

    # Rename group
    rename_resp = post_with_csrf(client, f"/admin/groups/{group.id}/rename", data={"name": "ИС 25-Новая"})
    assert rename_resp.status_code == 200

    # Assignment was moved
    assert len(store.list_for_group("ИС 25/9-1П")) == 0
    assert len(store.list_for_group("ИС 25-Новая")) == 1

    # Delete group
    delete_resp = post_with_csrf(client, f"/admin/groups/{group.id}/delete")
    assert delete_resp.status_code == 204

    # Assignment was deleted
    assert len(store.list_for_group("ИС 25-Новая")) == 0


def test_student_topic_select_preserves_clean_topic_name_and_backward_compatibility(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id and t.name == "Логические элементы. Базовые схемы")

    store = AssignmentStore(private_dir / "access.sqlite3")
    store.create("ИС 25/9-1П", subject.id, topic.id, "2026-10-14", 2)

    # Unlock via JSON
    unlock_res = post_with_csrf(
        client,
        "/unlock",
        data={"group": "ИС 25/9-1П", "password": "1234"},
        headers={"Accept": "application/json"},
    )
    assert unlock_res.status_code == 200
    assignment = unlock_res.json["assignments"][0]

    # Verify that clean topic name is submitted and accepted
    sub_data = {
        "group": "ИС 25/9-1П",
        "discipline": subject.name,
        "work": assignment["topic_name"],  # Clean topic name
        "student_name": "Иванов Иван Иванович",
        "file": (BytesIO(b"%PDF-1.4 test content"), "homework.pdf"),
    }
    submit_res = post_with_csrf(client, "/submit", data=sub_data)
    assert submit_res.status_code == 201
    assert list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))


def test_admin_assignments_page_authenticated(client_env):
    from app.catalog_store import create_catalog_store
    client, _ = client_env
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})

    resp = client.get("/admin/assignments")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Занятия и пары" in html
    assert "Учебная группа" in html
    assert "Дисциплина" in html

    catalog = create_catalog_store(client.application.config).snapshot()
    for group in catalog.groups:
        assert group.name in html
    for subject in catalog.subjects:
        assert subject.name in html

    # Calling with empty group also returns 200 cleanly
    resp_empty_group = client.get("/admin/assignments?group=")
    assert resp_empty_group.status_code == 200


def test_admin_assignments_catalog_validation(client_env):
    from uuid import uuid4

    from app.catalog_store import create_catalog_store
    client, _ = client_env
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})

    catalog = create_catalog_store(client.application.config).snapshot()
    subject1 = catalog.subjects[0]
    topic1 = next(t for t in catalog.topics if t.subject_id == subject1.id)
    subject2 = catalog.subjects[1]
    topic2 = next(t for t in catalog.topics if t.subject_id == subject2.id)
    valid_group = catalog.groups[0].name

    # 1. Non-existent group (400)
    resp = post_with_csrf(
        client,
        "/admin/assignments",
        data={
            "group_name": "Несуществующая группа",
            "subject_id": str(subject1.id),
            "topic_id": str(topic1.id),
            "lesson_date": "2026-10-15",
            "pair_number": "1",
        },
    )
    assert resp.status_code == 400

    # 2. Non-existent subject (400)
    resp = post_with_csrf(
        client,
        "/admin/assignments",
        data={
            "group_name": valid_group,
            "subject_id": str(uuid4()),
            "topic_id": str(topic1.id),
            "lesson_date": "2026-10-15",
            "pair_number": "1",
        },
    )
    assert resp.status_code == 400

    # 3. Mismatched topic_id not belonging to subject_id (400)
    resp = post_with_csrf(
        client,
        "/admin/assignments",
        data={
            "group_name": valid_group,
            "subject_id": str(subject1.id),
            "topic_id": str(topic2.id),
            "lesson_date": "2026-10-15",
            "pair_number": "1",
        },
    )
    assert resp.status_code == 400

    # 4. Create a valid assignment
    create_resp = post_with_csrf(
        client,
        "/admin/assignments",
        data={
            "group_name": valid_group,
            "subject_id": str(subject1.id),
            "topic_id": str(topic1.id),
            "lesson_date": "2026-10-15",
            "pair_number": "1",
        },
    )
    assert create_resp.status_code == 201
    assignment_id = create_resp.json["id"]

    # 5. Mismatched topic_id on update (400)
    update_resp = post_with_csrf(
        client,
        f"/admin/assignments/{assignment_id}/update",
        data={
            "topic_id": str(topic2.id),
            "lesson_date": "2026-10-15",
            "pair_number": "2",
        },
    )
    assert update_resp.status_code == 400


def test_subject_and_topic_delete_cascades_to_assignments(client_env):
    from uuid import UUID

    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    client, private_dir = client_env
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})

    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0]
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # 1. Create assignment for topic, then delete topic
    store = AssignmentStore(private_dir / "access.sqlite3")
    item = store.create(group.name, subject.id, topic.id, "2026-10-15", 1)
    assert store.get(item.id) is not None

    del_topic_resp = post_with_csrf(client, f"/admin/topics/{topic.id}/delete")
    assert del_topic_resp.status_code == 204
    assert store.get(item.id) is None

    # 2. Create subject and assignment, then delete subject
    new_subj_resp = post_with_csrf(client, "/admin/subjects", data={"name": "Предмет под снос"})
    assert new_subj_resp.status_code == 201
    new_subj_id = UUID(new_subj_resp.json["id"])

    # Associate an assignment with new_subj_id
    item2 = store.create(group.name, new_subj_id, UUID("00000000-0000-0000-0000-000000000001"), "2026-10-16", 1)
    assert store.get(item2.id) is not None

    del_subj_resp = post_with_csrf(client, f"/admin/subjects/{new_subj_id}/delete")
    assert del_subj_resp.status_code == 204
    assert store.get(item2.id) is None


def test_index_contains_accessible_submission_overlay(client_env):
    client, _ = client_env
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="submission_overlay"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'tabindex="-1"' in html
    assert 'id="overlay_loading"' in html
    assert 'id="overlay_success"' in html
    assert 'id="overlay_success_title"' in html
    assert 'id="overlay_error"' in html
    assert 'id="overlay_error_title"' in html
    assert 'role="alert"' in html
    assert 'aria-live="assertive"' in html
    assert 'id="overlay_filename"' in html
    assert 'id="overlay_reset_button"' in html
    assert 'id="overlay_retry_button"' in html


def test_submit_handles_http_errors_and_returns_status(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)

    # 1. Upload without group authorization returns 403
    assert client.post("/submit", data=valid_submission()).status_code == 400
    unauth_resp = post_with_csrf(client, "/submit", data=valid_submission())
    assert unauth_resp.status_code == 403
    unlock_response = post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert unlock_response.status_code == 204
    submit_response = post_with_csrf(client, "/submit", data=valid_submission())
    assert submit_response.status_code == 201

    assert submit_response.is_json
    data = submit_response.get_json()
    assert "filename" in data
    assert data["filename"] == "Иванов Иван Иванович 1.pdf"
    saved_file = (
        tmp_path
        / "uploads"
        / "ИС 25／9-1П"
        / "Архитектура аппаратных средств"
        / "Логические элементы. Базовые схемы"
        / "Иванов Иван Иванович 1.pdf"
    )
    assert saved_file.is_file()


def test_favicon_and_logo_assets_served(client_env):
    client, _ = client_env
    fav_resp = client.get("/favicon.ico")
    assert fav_resp.status_code == 200
    assert "image/" in fav_resp.content_type

    logo_resp = client.get("/static/logo.svg")
    assert logo_resp.status_code == 200
    assert "svg" in logo_resp.content_type


def test_brand_logo_rendered_in_templates(client_env):
    client, _ = client_env
    index_html = client.get("/").get_data(as_text=True)
    assert 'class="brand__logo"' in index_html
    assert 'logo.svg' in index_html
    assert 'favicon.svg' in index_html

    admin_html = client.get("/admin").get_data(as_text=True)
    assert 'class="brand__logo"' in admin_html
    assert 'logo.svg' in admin_html
    assert 'favicon.svg' in admin_html

    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    catalog_html = client.get("/admin/groups").get_data(as_text=True)
    assert 'class="brand__logo"' in catalog_html
    assert 'logo.svg' in catalog_html

    access_html = client.get("/admin/access").get_data(as_text=True)
    assert 'class="brand__logo"' in access_html
    assert 'logo.svg' in access_html

    assignments_html = client.get("/admin/assignments").get_data(as_text=True)
    assert 'class="brand__logo"' in assignments_html
    assert 'logo.svg' in assignments_html


def test_upload_mb_configuration_and_frontend_limit(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": catalog_path,
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
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": catalog_path,
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

    with pytest.raises(RuntimeError) as exc_info:
        create_app(cfg)

    msg = str(exc_info.value)
    assert msg == "SECRET_KEY must be a non-placeholder secret of at least 32 bytes"
    if isinstance(invalid_key, (str, bytes)) and invalid_key:
        key_str = invalid_key.decode() if isinstance(invalid_key, bytes) else invalid_key
        assert key_str not in msg
    assert not private_dir.exists()
    assert not storage_dir.exists()


def test_secret_key_validation_valid_formats(tmp_path: Path, monkeypatch):
    valid_str = "a" * 32
    app1 = create_app({
        "TESTING": True,
        "SECRET_KEY": valid_str,
        "PRIVATE_DATA_DIR": tmp_path / "p1",
    })
    assert app1.config["SECRET_KEY"] == valid_str

    valid_bytes = b"b" * 32
    app2 = create_app({
        "TESTING": True,
        "SECRET_KEY": valid_bytes,
        "PRIVATE_DATA_DIR": tmp_path / "p2",
    })
    assert app2.config["SECRET_KEY"] == valid_bytes

    monkeypatch.setenv("SECRET_KEY", "c" * 32)
    app3 = create_app({
        "TESTING": True,
        "PRIVATE_DATA_DIR": tmp_path / "p3",
    })
    assert app3.config["SECRET_KEY"] == "c" * 32


def test_old_cookie_signed_with_dev_secret_rejected(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())
    private_dir = tmp_path / ".private"

    # Create an app using old unsafe key and sign an admin session
    old_app = Flask("old_app")
    old_app.config["SECRET_KEY"] = "dev-secret"
    serializer = old_app.session_interface.get_signing_serializer(old_app)
    assert serializer is not None
    old_cookie_val = serializer.dumps({"admin": True})

    # Now create real app with secure key
    real_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": catalog_path,
    })
    client = real_app.test_client()
    client.set_cookie("session", old_cookie_val)
    resp = client.get("/admin/access", follow_redirects=False)
    assert resp.status_code == 403


def test_shared_secret_between_two_app_instances(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())
    private_dir = tmp_path / ".private"
    shared_secret = "shared-secret-must-be-at-least-32-bytes-long!"

    app1 = create_app({
        "TESTING": True,
        "SECRET_KEY": shared_secret,
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": catalog_path,
    })
    app2 = create_app({
        "TESTING": True,
        "SECRET_KEY": shared_secret,
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": catalog_path,
    })

    # Sign session in app1
    s1 = app1.session_interface.get_signing_serializer(app1)
    assert s1 is not None
    cookie_val = s1.dumps({"admin": True})

    # app2 verifies the session and grants access
    client2 = app2.test_client()
    client2.set_cookie("session", cookie_val)
    resp = client2.get("/admin/access")
    assert resp.status_code == 200


CSRF_MUTATION_ROUTES = [
    (
        "unlock",
        "/unlock",
        lambda ctx: {"group": ctx["group"].name, "password": "1234"},
        False,
        False,
    ),
    (
        "submit",
        "/submit",
        lambda ctx: {
            "group": ctx["group"].name,
            "discipline": ctx["subject"].name,
            "work": ctx["topic"].name,
            "student_name": "Иванов Иван Иванович",
            "file": (BytesIO(b"%PDF-1.4 test"), "submission.pdf"),
        },
        True,
        False,
    ),
    (
        "admin_login",
        "/admin/login",
        lambda ctx: {"password": "admin-secret"},
        False,
        False,
    ),
    (
        "admin_logout",
        "/admin/logout",
        lambda ctx: {},
        False,
        True,
    ),
    (
        "admin_access",
        "/admin/access",
        lambda ctx: {
            "group": ctx["group"].name,
            "password": "modified-password",
            "is_open": "1",
            "closes_at": "",
        },
        False,
        True,
    ),
    (
        "admin_catalog_create",
        "/admin/groups",
        lambda ctx: {"name": "Новая группа CSRF"},
        False,
        True,
    ),
    (
        "admin_catalog_mutate",
        lambda ctx: f"/admin/groups/{ctx['group'].id}/rename",
        lambda ctx: {"name": "Переименованная группа CSRF"},
        False,
        True,
    ),
    (
        "admin_topics_move",
        "/admin/topics/move",
        lambda ctx: {
            "topic_ids": [str(ctx["topic"].id)],
            "subject_id": str(ctx["other_subject"].id),
        },
        False,
        True,
    ),
    (
        "admin_assignments",
        "/admin/assignments",
        lambda ctx: {
            "group_name": ctx["group"].name,
            "subject_id": str(ctx["subject"].id),
            "topic_id": str(ctx["topic"].id),
            "lesson_date": "2026-10-25",
            "pair_number": "5",
        },
        False,
        True,
    ),
    (
        "admin_assignments_update",
        lambda ctx: f"/admin/assignments/{ctx['assignment'].id}/update",
        lambda ctx: {
            "lesson_date": "2026-10-26",
            "pair_number": "4",
            "topic_id": str(ctx["topic"].id),
        },
        False,
        True,
    ),
    (
        "admin_assignments_delete",
        lambda ctx: f"/admin/assignments/{ctx['assignment'].id}/delete",
        lambda ctx: {},
        False,
        True,
    ),
]


@pytest.mark.parametrize("condition", ["missing", "tampered", "foreign"])
@pytest.mark.parametrize(
    "route_name,url_pattern,payload_fn,need_student,need_admin",
    CSRF_MUTATION_ROUTES,
    ids=[r[0] for r in CSRF_MUTATION_ROUTES],
)
def test_csrf_mutation_routes_reject_invalid_tokens_without_state_change(
    client_env,
    route_name,
    url_pattern,
    payload_fn,
    need_student,
    need_admin,
    condition,
):
    client, private_dir = client_env
    configure_group(private_dir)
    app = client.application
    storage_root = app.config["STORAGE_ROOT"]
    cat_store = create_catalog_store(app.config)
    catalog = cat_store.snapshot()
    group = next(g for g in catalog.groups if g.name == "ИС 25/9-1П")
    subject = catalog.subjects[0]
    other_subject = catalog.subjects[1]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)
    assign_store = AssignmentStore(private_dir / "access.sqlite3")
    assignment = assign_store.create(group.name, subject.id, topic.id, "2026-10-14", 1)

    ctx = {
        "group": group,
        "subject": subject,
        "other_subject": other_subject,
        "topic": topic,
        "assignment": assignment,
    }

    url = url_pattern(ctx) if callable(url_pattern) else url_pattern
    data = payload_fn(ctx)

    if need_student:
        post_with_csrf(client, "/unlock", data={"group": group.name, "password": "1234"})
    if need_admin:
        post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})

    # Ensure client has session / CSRF token established
    get_csrf_token(client)

    headers = {"Accept": "application/json"}
    if condition == "tampered":
        headers["X-CSRFToken"] = "tampered-token"
    elif condition == "foreign":
        client2 = app.test_client()
        headers["X-CSRFToken"] = get_csrf_token(client2)

    # Capture initial state
    acc_store = AccessStore(private_dir / "access.sqlite3", [g.name for g in catalog.groups])
    before_catalog = cat_store.snapshot()
    before_access = acc_store.groups()
    before_assignments = assign_store.list_all()
    before_files = sorted(str(p.relative_to(storage_root)) for p in storage_root.rglob("*") if p.is_file())
    with client.session_transaction() as sess:
        before_admin = sess.get("admin")
        before_student_group = sess.get("student_group")

    # Send mutation request
    res = client.post(url, data=data, headers=headers)
    assert res.status_code == 400
    assert res.is_json
    assert res.json["error"] == "csrf_error"
    assert "Сессия устарела" in res.json["message"]

    # Verify zero changes occurred in catalog, access DB, assignments DB, storage files, session privileges
    assert cat_store.snapshot() == before_catalog
    assert acc_store.groups() == before_access
    assert assign_store.list_all() == before_assignments
    assert sorted(str(p.relative_to(storage_root)) for p in storage_root.rglob("*") if p.is_file()) == before_files
    with client.session_transaction() as sess:
        assert sess.get("admin") == before_admin
        assert sess.get("student_group") == before_student_group


def test_csrf_form_token_vs_header_token_both_pass(client_env):
    client, private_dir = client_env
    configure_group(private_dir)

    # 1. Form token in form body
    client1 = client.application.test_client()
    token1 = get_csrf_token(client1)
    res_form = client1.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234", "csrf_token": token1})
    assert res_form.status_code == 204
    with client1.session_transaction() as sess:
        assert sess.get("student_group") == "ИС 25/9-1П"

    # 2. Header token in X-CSRFToken
    client2 = client.application.test_client()
    token2 = get_csrf_token(client2)
    res_hdr = client2.post(
        "/unlock",
        data={"group": "ИС 25/9-1П", "password": "1234"},
        headers={"X-CSRFToken": token2},
    )
    assert res_hdr.status_code == 204
    with client2.session_transaction() as sess:
        assert sess.get("student_group") == "ИС 25/9-1П"

    # Also test admin login with form token and header token
    client3 = client.application.test_client()
    token3 = get_csrf_token(client3, "/admin")
    res_login_form = client3.post("/admin/login", data={"password": "admin-secret", "csrf_token": token3})
    assert res_login_form.status_code == 204
    with client3.session_transaction() as sess:
        assert sess.get("admin") is True

    client4 = client.application.test_client()
    token4 = get_csrf_token(client4, "/admin")
    res_login_hdr = client4.post(
        "/admin/login",
        data={"password": "admin-secret"},
        headers={"X-CSRFToken": token4},
    )
    assert res_login_hdr.status_code == 204
    with client4.session_transaction() as sess:
        assert sess.get("admin") is True


def test_csrf_login_and_unlock_before_authorization(client_env):
    client, private_dir = client_env
    configure_group(private_dir)

    # Unlock before authorization
    student_client = client.application.test_client()
    with student_client.session_transaction() as sess:
        assert "student_group" not in sess
    token_unlock = get_csrf_token(student_client, "/")
    unlock_res = student_client.post(
        "/unlock",
        data={"group": "ИС 25/9-1П", "password": "1234"},
        headers={"X-CSRFToken": token_unlock},
    )
    assert unlock_res.status_code == 204
    with student_client.session_transaction() as sess:
        assert sess.get("student_group") == "ИС 25/9-1П"

    # Admin login before authorization
    admin_client = client.application.test_client()
    with admin_client.session_transaction() as sess:
        assert not sess.get("admin")
    token_login = get_csrf_token(admin_client, "/admin")
    login_res = admin_client.post(
        "/admin/login",
        data={"password": "admin-secret"},
        headers={"X-CSRFToken": token_login},
    )
    assert login_res.status_code == 204
    with admin_client.session_transaction() as sess:
        assert sess.get("admin") is True


def test_csrf_logout_without_token_does_not_log_out_and_with_token_logs_out(client_env):
    client, _ = client_env
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    with client.session_transaction() as sess:
        assert sess.get("admin") is True

    # Logout without CSRF token fails (400) and session["admin"] remains True
    res_missing = client.post("/admin/logout", headers={"Accept": "application/json"})
    assert res_missing.status_code == 400
    assert res_missing.json["error"] == "csrf_error"
    with client.session_transaction() as sess:
        assert sess.get("admin") is True

    # Logout with tampered CSRF token fails (400) and session["admin"] remains True
    res_tampered = client.post(
        "/admin/logout",
        headers={"X-CSRFToken": "tampered", "Accept": "application/json"},
    )
    assert res_tampered.status_code == 400
    assert res_tampered.json["error"] == "csrf_error"
    with client.session_transaction() as sess:
        assert sess.get("admin") is True

    # Logout with valid CSRF token succeeds (302 redirect) and session["admin"] removed
    res_valid = post_with_csrf(client, "/admin/logout")
    assert res_valid.status_code == 302
    with client.session_transaction() as sess:
        assert "admin" not in sess


def test_csrf_authenticated_admin_cookie_without_csrf_token_cannot_mutate(client_env):
    client, private_dir = client_env
    configure_group(private_dir)
    post_with_csrf(client, "/admin/login", data={"password": "admin-secret"})
    with client.session_transaction() as sess:
        assert sess.get("admin") is True

    # Authenticated admin cookie is present, but missing CSRF token must block mutations
    headers = {"Accept": "application/json"}
    assert client.post("/admin/groups", data={"name": "Attacked"}, headers=headers).status_code == 400
    assert client.post("/admin/access", data={"group": "ИС 25/9-1П", "is_open": "0"}, headers=headers).status_code == 400
    assert client.post("/admin/assignments", data={"group_name": "ИС 25/9-1П"}, headers=headers).status_code == 400
    assert client.post("/admin/logout", headers=headers).status_code == 400


def test_csrf_expired_token_rejected_deterministically(client_env, monkeypatch):
    client, private_dir = client_env
    configure_group(private_dir)

    # 1. Deterministic expiration via monkeypatched time.time
    t0 = 1700000000.0
    monkeypatch.setattr(time, "time", lambda: t0)
    token = get_csrf_token(client)
    # Advance time beyond WTF_CSRF_TIME_LIMIT (3600 seconds)
    monkeypatch.setattr(time, "time", lambda: t0 + 4000.0)
    res_expired_time = client.post(
        "/unlock",
        data={"group": "ИС 25/9-1П", "password": "1234"},
        headers={"X-CSRFToken": token, "Accept": "application/json"},
    )
    assert res_expired_time.status_code == 400
    assert res_expired_time.is_json
    assert res_expired_time.json["error"] == "csrf_error"
    assert "Сессия устарела" in res_expired_time.json["message"]

    # 2. Deterministic expiration via WTF_CSRF_TIME_LIMIT = -1
    client2 = client.application.test_client()
    monkeypatch.setattr(time, "time", lambda: t0)
    token2 = get_csrf_token(client2)
    client2.application.config["WTF_CSRF_TIME_LIMIT"] = -1
    res_expired_cfg = client2.post(
        "/unlock",
        data={"group": "ИС 25/9-1П", "password": "1234"},
        headers={"X-CSRFToken": token2, "Accept": "application/json"},
    )
    assert res_expired_cfg.status_code == 400
    assert res_expired_cfg.is_json
    assert res_expired_cfg.json["error"] == "csrf_error"
    assert "Сессия устарела" in res_expired_cfg.json["message"]



def test_submit_allowed_extension_default_pdf(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})

    content = b"%PDF-1.4 test binary data"
    payload = valid_submission()
    payload["file"] = (BytesIO(content), "solution.pdf")

    res = post_with_csrf(client, "/submit", data=payload)
    assert res.status_code == 201
    assert res.json == {"filename": "Иванов Иван Иванович 1.pdf"}

    saved_files = list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == content


def test_submit_disallowed_extension_rejected(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})

    uploads_dir = tmp_path / "uploads"
    payload = valid_submission()
    payload["file"] = (BytesIO(b"MZ\x90\x00executable"), "payload.exe")

    res = post_with_csrf(client, "/submit", data=payload)
    assert res.status_code == 400
    assert res.is_json
    assert res.json == {
        "error": "file_type_not_allowed",
        "message": "Недопустимый формат файла.",
    }

    # Verify no directory or file created in storage
    if uploads_dir.exists():
        assert list(uploads_dir.rglob("*")) == []

    # Verify attempt number was not consumed: subsequent valid submission gets attempt 1
    valid_payload = valid_submission()
    valid_payload["file"] = (BytesIO(b"valid"), "solution.pdf")
    res_valid = post_with_csrf(client, "/submit", data=valid_payload)
    assert res_valid.status_code == 201
    assert res_valid.json == {"filename": "Иванов Иван Иванович 1.pdf"}


def test_submit_allowed_extension_default_py(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})

    content = b"print('hello world')\n"
    payload = valid_submission()
    payload["file"] = (BytesIO(content), "solution.py")

    res = post_with_csrf(client, "/submit", data=payload)
    assert res.status_code == 201
    assert res.json == {"filename": "Иванов Иван Иванович 1.py"}

    saved_files = list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.py"))
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == content


def test_submit_config_override_allowed_extensions(tmp_path: Path):
    private_dir = tmp_path / ".private"
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": catalog_path,
        "ALLOWED_UPLOAD_EXTENSIONS": ".pdf",
    })
    client = app.test_client()
    configure_group(private_dir)
    post_with_csrf(client, "/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})

    # .py should be rejected because only .pdf is allowed
    payload_py = valid_submission()
    payload_py["file"] = (BytesIO(b"print('test')"), "script.py")
    res_py = post_with_csrf(client, "/submit", data=payload_py)
    assert res_py.status_code == 400
    assert res_py.is_json
    assert res_py.json == {
        "error": "file_type_not_allowed",
        "message": "Недопустимый формат файла.",
    }

    # .pdf should be accepted and get attempt 1
    payload_pdf = valid_submission()
    payload_pdf["file"] = (BytesIO(b"pdf data"), "report.pdf")
    res_pdf = post_with_csrf(client, "/submit", data=payload_pdf)
    assert res_pdf.status_code == 201
    assert res_pdf.json == {"filename": "Иванов Иван Иванович 1.pdf"}


@pytest.mark.parametrize(
    "invalid_ext",
    [
        "*",
        "",
        "   ",
        ".pdf, , .docx",
        "pdf",
        None,
        [".pdf", "docx"],
        [".pdf", ""],
    ],
)
def test_create_app_invalid_allowed_upload_extensions_raises_runtime_error(tmp_path: Path, invalid_ext):
    private_dir = tmp_path / ".private"
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid upload extension"):
        create_app({
            "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
            "PRIVATE_DATA_DIR": private_dir,
            "CATALOG_PATH": catalog_path,
            "ALLOWED_UPLOAD_EXTENSIONS": invalid_ext,
        })
    # Stores must not have opened
    assert not private_dir.exists()


def test_create_app_allowed_upload_extensions_from_env(tmp_path: Path, monkeypatch):
    private_dir = tmp_path / ".private"
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())

    monkeypatch.setenv("ALLOWED_UPLOAD_EXTENSIONS", ".pdf, .DOCX")
    app = create_app({
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "PRIVATE_DATA_DIR": private_dir,
        "CATALOG_PATH": catalog_path,
    })
    assert app.config["ALLOWED_UPLOAD_EXTENSIONS"] == frozenset({".pdf", ".docx"})


def test_create_app_invalid_allowed_upload_extensions_from_env_raises(tmp_path: Path, monkeypatch):
    private_dir = tmp_path / ".private"
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("ALLOWED_UPLOAD_EXTENSIONS", "*")
    with pytest.raises(RuntimeError, match="Invalid upload extension"):
        create_app({
            "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
            "PRIVATE_DATA_DIR": private_dir,
            "CATALOG_PATH": catalog_path,
        })
    assert not private_dir.exists()


def test_index_page_contains_accept_attribute_and_allowed_extensions_hint(client_env):
    client, _ = client_env
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    app = client.application
    expected_accept = ",".join(sorted(app.config["ALLOWED_UPLOAD_EXTENSIONS"]))
    assert f'accept="{expected_accept}"' in html
    assert f'<input id="file" name="file" type="file" accept="{expected_accept}"' in html

    expected_hint = ", ".join(sorted(app.config["ALLOWED_UPLOAD_EXTENSIONS"]))
    assert "Допустимые форматы: " in html
    assert f'<span class="field__hint">Допустимые форматы: {expected_hint}</span>' in html


def test_index_page_with_custom_allowed_extensions(tmp_path: Path):
    private_dir = tmp_path / ".private"
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "CATALOG_PATH": catalog_path,
        "ALLOWED_UPLOAD_EXTENSIONS": ".pdf, .zip",
    })
    client = app.test_client()
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'accept=".pdf,.zip"' in html
    assert '<span class="field__hint">Допустимые форматы: .pdf, .zip</span>' in html


def test_session_cookie_contains_httponly_and_samesite_lax(client_env):
    client, private_dir = client_env
    configure_group(private_dir)

    # 1. Login session cookie
    login_resp = post_with_csrf(client, "/admin/login", csrf_page="/admin", data={"password": "admin-secret"})
    assert login_resp.status_code == 204
    login_set_cookies = login_resp.headers.getlist("Set-Cookie")
    session_cookies = [c for c in login_set_cookies if c.startswith("session=")]
    assert session_cookies, f"Expected session cookie in Set-Cookie: {login_set_cookies}"
    assert "HttpOnly" in session_cookies[0]
    assert "SameSite=Lax" in session_cookies[0]

    # 2. Unlock session cookie
    student_client = client.application.test_client()
    unlock_resp = post_with_csrf(student_client, "/unlock", csrf_page="/", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert unlock_resp.status_code == 204
    unlock_set_cookies = unlock_resp.headers.getlist("Set-Cookie")
    student_session_cookies = [c for c in unlock_set_cookies if c.startswith("session=")]
    assert student_session_cookies, f"Expected session cookie in Set-Cookie: {unlock_set_cookies}"
    assert "HttpOnly" in student_session_cookies[0]
    assert "SameSite=Lax" in student_session_cookies[0]


def test_create_app_session_cookie_defaults_and_override():
    app = create_app({"SECRET_KEY": "a" * 32})
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    app_override = create_app({
        "SECRET_KEY": "b" * 32,
        "SESSION_COOKIE_SAMESITE": "Strict",
        "SESSION_COOKIE_HTTPONLY": False,
    })
    assert app_override.config["SESSION_COOKIE_HTTPONLY"] is False
    assert app_override.config["SESSION_COOKIE_SAMESITE"] == "Strict"
