from concurrent.futures import ThreadPoolExecutor
from threading import Event
from io import BytesIO
from pathlib import Path

import pytest
from app.access import AccessStore
from app.web import create_app


@pytest.fixture
def client_env(tmp_path: Path):
    private_dir = tmp_path / ".private"
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes((Path(__file__).resolve().parents[1] / "catalog.json").read_bytes())
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "secret",
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
    assert client.post("/submit", data=valid_submission()).status_code == 403


def test_student_can_upload_only_after_unlocking_the_selected_open_group(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)

    unlock_response = client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert unlock_response.status_code == 204

    response = client.post("/submit", data=valid_submission())
    assert response.status_code == 201
    assert list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))


def test_upload_is_denied_when_the_student_session_group_differs(client_env):
    client, private_dir = client_env
    configure_group(private_dir)

    client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    payload = valid_submission()
    payload["group"] = "ИС 25/9-2П"

    assert client.post("/submit", data=payload).status_code == 403


def test_reconfiguring_group_invalidates_unlocked_student(client_env):
    client, private_dir = client_env
    configure_group(private_dir)

    client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    configure_group(private_dir, password="5678", is_open=False)

    assert client.post("/submit", data=valid_submission()).status_code == 403


def test_admin_login_is_required_to_access_and_change_groups(client_env):
    client, _ = client_env
    assert client.post("/admin/access", data={"group": "ИС 25/9-1П", "is_open": "1"}).status_code == 403
    assert client.post("/admin/login", data={"password": "wrong"}).status_code == 403
    assert client.post("/admin/login", data={"password": "admin-secret"}).status_code == 204


def test_admin_can_open_group_and_student_can_unlock_and_submit(client_env, tmp_path):
    client, _ = client_env
    assert client.post("/admin/login", data={"password": "admin-secret"}).status_code == 204

    save_response = client.post("/admin/access", data={
        "group": "ИС 25/9-1П",
        "password": "group-password",
        "is_open": "1",
        "closes_at": "",
    })
    assert save_response.status_code == 204

    unlock_response = client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "group-password"})
    assert unlock_response.status_code == 204

    submit_response = client.post("/submit", data=valid_submission())
    assert submit_response.status_code == 201
    assert list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))


def test_admin_page_shows_all_catalog_groups(client_env):
    client, _ = client_env
    client.post("/admin/login", data={"password": "admin-secret"})
    page = client.get("/admin/access").get_data(as_text=True)

    assert "ИС 25/9-1П" in page
    assert "ИС 25/9-2П" in page


def test_admin_setting_past_deadline_immediately_blocks_submission(client_env):
    client, _ = client_env
    client.post("/admin/login", data={"password": "admin-secret"})
    client.post("/admin/access", data={
        "group": "ИС 25/9-1П",
        "password": "group-password",
        "is_open": "1",
        "closes_at": "2020-01-01T00:00",
    })

    assert client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "group-password"}).status_code == 403


@pytest.mark.parametrize("kind", ("groups", "subjects"))
def test_admin_can_create_rename_and_delete_catalog_item(client_env, kind):
    client, _ = client_env
    assert client.get(f"/admin/{kind}").status_code == 403
    assert client.post(f"/admin/{kind}", data={"name": "Тест"}).status_code == 403
    client.post("/admin/login", data={"password": "admin-secret"})
    created = client.post(f"/admin/{kind}", data={"name": "Тест"})
    assert created.status_code == 201
    uid = created.json["id"]
    client.post("/admin/logout")
    assert client.post(f"/admin/{kind}/{uid}/rename", data={"name": "Чужое"}).status_code == 403
    assert client.post(f"/admin/{kind}/{uid}/delete").status_code == 403
    client.post("/admin/login", data={"password": "admin-secret"})
    duplicate = client.post(f"/admin/{kind}", data={"name": "тест"})
    assert duplicate.status_code == 400
    assert duplicate.json["error"] == "name_conflict"
    assert client.post(f"/admin/{kind}/{uid}/rename", data={"name": "Переименовано"}).status_code == 200
    assert "Переименовано" in client.get(f"/admin/{kind}").get_data(as_text=True)
    assert client.post(f"/admin/{kind}/{uid}/delete").status_code == 204
    assert client.post(f"/admin/{kind}/{uid}/delete").status_code == 404


def test_admin_topics_require_subject_and_support_single_and_bulk_mapping(client_env):
    client, _ = client_env
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    source, target = store.list_subjects()[:2]
    assert client.post("/admin/topics", data={"name": "Новая тема", "subject_id": str(source.id)}).status_code == 403
    client.post("/admin/login", data={"password": "admin-secret"})
    assert client.post("/admin/topics", data={"name": "Без владельца"}).status_code == 400
    created = client.post("/admin/topics", data={"name": "Новая тема", "subject_id": str(source.id)})
    assert created.status_code == 201
    uid = created.json["id"]
    assert created.json["subject_id"] == str(source.id)
    assert client.post("/admin/topics", data={"name": "новая ТЕМА", "subject_id": str(source.id)}).status_code == 400
    assert client.post("/admin/topics", data={"name": "Новая тема", "subject_id": str(target.id)}).status_code == 201
    updated = client.post(f"/admin/topics/{uid}/update", data={
        "name": "Другое название", "subject_id": str(target.id),
    })
    assert updated.status_code == 200
    assert updated.json["subject_id"] == str(target.id)
    client.post("/admin/logout")
    assert client.post("/admin/topics/move", data={
        "topic_ids": [uid], "subject_id": str(source.id),
    }).status_code == 403
    assert client.post(f"/admin/topics/{uid}/update", data={
        "name": "Чужое", "subject_id": str(source.id),
    }).status_code == 403
    client.post("/admin/login", data={"password": "admin-secret"})
    moved = client.post("/admin/topics/move", data={
        "topic_ids": [uid], "subject_id": str(source.id),
    })
    assert moved.status_code == 200
    assert moved.json["topics"][0]["subject_id"] == str(source.id)
    assert client.post(f"/admin/topics/{uid}/delete").status_code == 204


def test_bulk_topic_conflict_and_subject_delete_keep_catalog_unchanged(client_env):
    client, _ = client_env
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    first, second = store.list_subjects()[:2]
    client.post("/admin/login", data={"password": "admin-secret"})
    left = client.post("/admin/topics", data={"name": "Общий заголовок", "subject_id": str(first.id)}).json["id"]
    right = client.post("/admin/topics", data={"name": "Общий заголовок", "subject_id": str(second.id)}).json["id"]
    before = store.snapshot()
    conflict = client.post("/admin/topics/move", data={
        "topic_ids": [left, right], "subject_id": str(store.snapshot().unassigned_subject_id),
    })
    assert conflict.status_code == 400
    assert conflict.json["error"] == "topic_name_conflict"
    assert "Общий заголовок" in conflict.json["names"]
    assert store.snapshot() == before
    other = client.post("/admin/topics", data={
        "name": "Логические элементы. Базовые схемы", "subject_id": str(store.snapshot().unassigned_subject_id),
    })
    assert other.status_code == 201
    before = store.snapshot()
    conflict = client.post(f"/admin/subjects/{first.id}/delete")
    assert conflict.status_code == 400
    assert conflict.json["error"] == "topic_name_conflict"
    assert store.snapshot() == before
    client.post(f"/admin/topics/{other.json['id']}/delete")
    assert client.post(f"/admin/subjects/{first.id}/delete").status_code == 204
    assert all(topic.subject_id != first.id for topic in store.list_topics())
    assert client.post(f"/admin/subjects/{store.snapshot().unassigned_subject_id}/delete").status_code == 400


def test_deleted_group_removes_access_and_files_survive_recreation(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    assert client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 204
    assert client.post("/submit", data=valid_submission()).status_code == 201
    saved_files = list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))
    assert len(saved_files) == 1
    client.post("/admin/login", data={"password": "admin-secret"})
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    uid = next(item.id for item in store.list_groups() if item.name == "ИС 25/9-1П")
    assert client.post(f"/admin/groups/{uid}/delete").status_code == 204
    assert "ИС 25/9-1П" not in {item.group for item in AccessStore(private_dir / "access.sqlite3", []).groups()}
    assert client.post("/admin/groups", data={"name": "ИС 25/9-1П"}).status_code == 201
    assert client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 403
    assert client.post("/submit", data=valid_submission()).status_code == 403
    assert saved_files[0].read_bytes() == b"answer"


def test_renaming_group_preserves_password_and_revokes_student_session(client_env):
    client, private_dir = client_env
    configure_group(private_dir)
    assert client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 204
    client.post("/admin/login", data={"password": "admin-secret"})
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    uid = next(item.id for item in store.list_groups() if item.name == "ИС 25/9-1П")
    response = client.post(f"/admin/groups/{uid}/rename", data={"name": "Группа П"})
    assert response.status_code == 200
    assert client.post("/submit", data=valid_submission()).status_code == 403
    assert client.post("/unlock", data={"group": "Группа П", "password": "1234"}).status_code == 204
    submission = valid_submission()
    submission["group"] = "Группа П"
    assert client.post("/submit", data=submission).status_code == 201


def test_group_rename_keeps_access_when_access_page_reads_concurrently(client_env, monkeypatch):
    client, private_dir = client_env
    configure_group(private_dir)
    client.post("/admin/login", data={"password": "admin-secret"})
    other = client.application.test_client()
    other.post("/admin/login", data={"password": "admin-secret"})
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
        response = client.post(f"/admin/groups/{uid}/rename", data={"name": "Новая группа"})
        assert result.result(timeout=5) == 200
    assert response.status_code == 200
    assert client.post("/unlock", data={"group": "Новая группа", "password": "1234"}).status_code == 204


def test_unlock_cannot_bind_old_password_to_recreated_group(client_env, monkeypatch):
    student, private_dir = client_env
    configure_group(private_dir)
    admin = student.application.test_client()
    admin.post("/admin/login", data={"password": "admin-secret"})
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
                assert admin.post(f"/admin/groups/{uid}/delete").status_code == 204
                assert admin.post("/admin/groups", data={"name": "ИС 25/9-1П"}).status_code == 201
                assert admin.post("/admin/access", data={
                    "group": "ИС 25/9-1П", "password": "different", "is_open": "1",
                }).status_code == 204
            finally:
                admin_done.set()

        result = pool.submit(recreate)
        assert student.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 204
        result.result(timeout=5)
    assert student.post("/submit", data=valid_submission()).status_code == 403


def test_deleted_topic_blocks_new_submission_without_deleting_previous_upload(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert client.post("/submit", data=valid_submission()).status_code == 201
    client.post("/admin/login", data={"password": "admin-secret"})
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    uid = next(item.id for item in store.list_topics() if item.name == "Логические элементы. Базовые схемы")
    assert client.post(f"/admin/topics/{uid}/delete").status_code == 204
    assert client.post("/submit", data=valid_submission()).status_code == 400
    assert list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))


def test_new_subject_and_topic_require_matching_relationship(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    from app.catalog_store import create_catalog_store
    store = create_catalog_store(client.application.config)
    other_subject = store.list_subjects()[0]
    client.post("/admin/login", data={"password": "admin-secret"})
    subject = client.post("/admin/subjects", data={"name": "Новый предмет"}).json
    topic = client.post("/admin/topics", data={
        "name": "Новая тема", "subject_id": subject["id"],
    }).json
    client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})

    submission = valid_submission()
    submission["discipline"] = subject["name"]
    submission["work"] = topic["name"]
    assert client.post("/submit", data=submission).status_code == 201
    original_path = next((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))

    assert client.post(f"/admin/topics/{topic['id']}/update", data={
        "name": topic["name"], "subject_id": str(other_subject.id),
    }).status_code == 200
    mismatched_submission = valid_submission()
    mismatched_submission["discipline"] = subject["name"]
    mismatched_submission["work"] = topic["name"]
    assert client.post("/submit", data=mismatched_submission).status_code == 400
    assert original_path.exists()

    matching_submission = valid_submission()
    matching_submission["discipline"] = other_subject.name
    matching_submission["work"] = topic["name"]
    assert client.post("/submit", data=matching_submission).status_code == 201
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
    resp_204 = client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert resp_204.status_code == 204

    # Post with Accept: application/json gives 200 with assignments payload
    resp_json = client.post(
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
    unlock_resp = client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
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
    assert client.post("/admin/assignments", data={}).status_code == 403
    assert client.post("/admin/assignments/11111111-1111-1111-1111-111111111111/update", data={}).status_code == 403
    assert client.post("/admin/assignments/11111111-1111-1111-1111-111111111111/delete").status_code == 403


def test_admin_assignments_crud_and_conflict(client_env):
    from uuid import UUID
    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    client, private_dir = client_env
    client.post("/admin/login", data={"password": "admin-secret"})

    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # 1. Create assignment (201)
    create_resp = client.post(
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
    conflict_resp = client.post(
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
    bad_resp = client.post(
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
    create2_resp = client.post(
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
    update_resp = client.post(
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
    conflict_update = client.post(
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
    del_resp = client.post(f"/admin/assignments/{created_id}/delete")
    assert del_resp.status_code == 204

    store = AssignmentStore(private_dir / "access.sqlite3")
    assert store.get(UUID(created_id)) is None
    assert store.get(UUID(id2)) is not None


def test_group_rename_and_delete_cascades_to_assignments(client_env):
    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    client, private_dir = client_env
    client.post("/admin/login", data={"password": "admin-secret"})

    catalog = create_catalog_store(client.application.config).snapshot()
    group = next(g for g in catalog.groups if g.name == "ИС 25/9-1П")
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    store = AssignmentStore(private_dir / "access.sqlite3")
    store.create("ИС 25/9-1П", subject.id, topic.id, "2026-10-14", 1)

    # Rename group
    rename_resp = client.post(f"/admin/groups/{group.id}/rename", data={"name": "ИС 25-Новая"})
    assert rename_resp.status_code == 200

    # Assignment was moved
    assert len(store.list_for_group("ИС 25/9-1П")) == 0
    assert len(store.list_for_group("ИС 25-Новая")) == 1

    # Delete group
    delete_resp = client.post(f"/admin/groups/{group.id}/delete")
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
    unlock_res = client.post(
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
    submit_res = client.post("/submit", data=sub_data)
    assert submit_res.status_code == 201
    assert list((tmp_path / "uploads").rglob("Иванов Иван Иванович 1.pdf"))


def test_admin_assignments_page_authenticated(client_env):
    from app.catalog_store import create_catalog_store
    client, _ = client_env
    client.post("/admin/login", data={"password": "admin-secret"})

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
    client.post("/admin/login", data={"password": "admin-secret"})

    catalog = create_catalog_store(client.application.config).snapshot()
    subject1 = catalog.subjects[0]
    topic1 = next(t for t in catalog.topics if t.subject_id == subject1.id)
    subject2 = catalog.subjects[1]
    topic2 = next(t for t in catalog.topics if t.subject_id == subject2.id)
    valid_group = catalog.groups[0].name

    # 1. Non-existent group (400)
    resp = client.post(
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
    resp = client.post(
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
    resp = client.post(
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
    create_resp = client.post(
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
    update_resp = client.post(
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
    client.post("/admin/login", data={"password": "admin-secret"})

    catalog = create_catalog_store(client.application.config).snapshot()
    group = catalog.groups[0]
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # 1. Create assignment for topic, then delete topic
    store = AssignmentStore(private_dir / "access.sqlite3")
    item = store.create(group.name, subject.id, topic.id, "2026-10-15", 1)
    assert store.get(item.id) is not None

    del_topic_resp = client.post(f"/admin/topics/{topic.id}/delete")
    assert del_topic_resp.status_code == 204
    assert store.get(item.id) is None

    # 2. Create subject and assignment, then delete subject
    new_subj_resp = client.post("/admin/subjects", data={"name": "Предмет под снос"})
    assert new_subj_resp.status_code == 201
    new_subj_id = UUID(new_subj_resp.json["id"])

    # Associate an assignment with new_subj_id
    item2 = store.create(group.name, new_subj_id, UUID("00000000-0000-0000-0000-000000000001"), "2026-10-16", 1)
    assert store.get(item2.id) is not None

    del_subj_resp = client.post(f"/admin/subjects/{new_subj_id}/delete")
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
    assert 'id="overlay_loading"' in html
    assert 'id="overlay_success"' in html
    assert 'id="overlay_error"' in html
    assert 'id="overlay_filename"' in html
    assert 'id="overlay_reset_button"' in html
    assert 'id="overlay_retry_button"' in html
