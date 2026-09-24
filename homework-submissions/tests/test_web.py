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


@pytest.mark.parametrize("kind", ("groups", "subjects", "topics"))
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
    assert client.post(f"/admin/{kind}", data={"name": "тест"}).status_code == 400
    assert client.post(f"/admin/{kind}/{uid}/rename", data={"name": "Переименовано"}).status_code == 200
    assert "Переименовано" in client.get(f"/admin/{kind}").get_data(as_text=True)
    assert client.post(f"/admin/{kind}/{uid}/delete").status_code == 204
    assert client.post(f"/admin/{kind}/{uid}/delete").status_code == 404


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


def test_new_subject_and_topic_can_be_submitted_without_relationship(client_env):
    client, private_dir = client_env
    configure_group(private_dir)
    client.post("/admin/login", data={"password": "admin-secret"})
    assert client.post("/admin/subjects", data={"name": "Новый предмет"}).status_code == 201
    assert client.post("/admin/topics", data={"name": "Новая тема"}).status_code == 201
    assert client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 204
    submission = valid_submission()
    submission["discipline"] = "Новый предмет"
    submission["work"] = "Новая тема"
    assert client.post("/submit", data=submission).status_code == 201
