import sys
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_dir))

from app.catalog_store import create_catalog_store

catalog_path = repo_dir / "catalog.json"
if not catalog_path.is_file():
    create_catalog_store({"CATALOG_PROVIDER": "json", "CATALOG_PATH": catalog_path}).snapshot()
from io import BytesIO

import pytest
from app.access import AccessStore
from app.web import create_app


@pytest.fixture
def client_env(tmp_path: Path):
    private_dir = tmp_path / ".private"
    source = repo_dir / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    test_catalog_path = tmp_path / "catalog.json"
    test_catalog_path.write_bytes(source.read_bytes())
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "secret",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": test_catalog_path,
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
