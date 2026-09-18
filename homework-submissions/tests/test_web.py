from io import BytesIO
from pathlib import Path

import pytest
from app.access import AccessStore
from app.web import create_app


@pytest.fixture
def client_env(tmp_path: Path):
    private_dir = tmp_path / ".private"
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "secret",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": Path(__file__).resolve().parents[1] / "catalog.json",
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
