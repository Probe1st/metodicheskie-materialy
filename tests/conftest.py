from __future__ import annotations

import sys
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import pytest

repo_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_dir))

from app.access import AccessStore
from app.database import initialize
from app.sqlite_catalog import SqliteCatalogStore
from app.web import create_app


class CSRFTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "meta" and attrs_dict.get("name") == "csrf-token":
            self.token = attrs_dict.get("content")
        elif tag == "input" and attrs_dict.get("name") == "csrf_token" and not self.token:
            self.token = attrs_dict.get("value")


def get_csrf_token(client, page="/"):
    resp = client.get(page)
    parser = CSRFTokenParser()
    parser.feed(resp.get_data(as_text=True))
    return parser.token


def post_with_csrf(client, url, *, csrf_page="/", **kwargs):
    headers = dict(kwargs.get("headers") or {})
    if "X-CSRFToken" not in headers:
        token = get_csrf_token(client, csrf_page)
        if token:
            headers["X-CSRFToken"] = token
    kwargs["headers"] = headers
    return client.post(url, **kwargs)


@pytest.fixture(name="post_with_csrf")
def post_with_csrf_fixture():
    return post_with_csrf


@pytest.fixture
def client_env(tmp_path: Path):
    private_dir = tmp_path / ".private"
    private_dir.mkdir(parents=True, exist_ok=True)
    db_path = private_dir / "app_v2.sqlite3"
    storage_root = tmp_path / "uploads"
    storage_root.mkdir(parents=True, exist_ok=True)

    initialize(db_path)
    cat = SqliteCatalogStore(db_path)
    cat.create_group("ИС 25/9-1П")
    cat.create_group("ИС 25/9-2П")
    subj1 = cat.create_subject("Архитектура аппаратных средств")
    cat.create_topic(subj1.id, "Логические элементы. Базовые схемы")
    cat.create_topic(subj1.id, "Комбинационные устройства")
    subj2 = cat.create_subject("Базы данных")
    cat.create_topic(subj2.id, "Проектирование ER-моделей")

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "MAX_UPLOAD_GB": 1,
        "WTF_CSRF_ENABLED": False,
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
    db_path = private_dir / "app_v2.sqlite3"
    cat = SqliteCatalogStore(db_path)
    grp = next((g for g in cat.list_groups() if g.name == group), None)
    if not grp:
        grp = cat.create_group(group)
    store = AccessStore(db_path)
    store.configure(grp.id, is_open=is_open, password=password)
