from io import BytesIO
from pathlib import Path

import pytest
from app.web import create_app


@pytest.fixture
def client(tmp_path: Path):
    app = create_app({"TESTING": True, "SECRET_KEY": "secret", "SUBMISSION_PASSWORD": "teacher", "STORAGE_ROOT": tmp_path, "CATALOG_PATH": Path("catalog.json"), "MAX_UPLOAD_GB": 1})
    return app.test_client()


def unlock(client):
    return client.post("/unlock", data={"password": "teacher"})


def test_submit_requires_password(client):
    assert client.post("/submit").status_code == 403


def test_submit_saves_valid_file(client, tmp_path):
    unlock(client)
    response = client.post("/submit", data={"group": "ИС 25/9-1П", "discipline": "Архитектура аппаратных средств", "work": "Логические элементы. Базовые схемы", "student_name": "Иванов Иван Иванович", "file": (BytesIO(b"answer"), "answer.pdf")})
    assert response.status_code == 201
    assert list(tmp_path.rglob("Иванов Иван Иванович 1.pdf"))
