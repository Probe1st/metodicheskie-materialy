import sys
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import pytest

repo_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_dir))

from app.access import AccessStore
from app.catalog_store import create_catalog_store
from app.web import create_app

catalog_path = repo_dir / "catalog.json"
if not catalog_path.is_file():
    create_catalog_store({"CATALOG_PROVIDER": "json", "CATALOG_PATH": catalog_path}).snapshot()


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
    source = repo_dir / "catalog.json"
    if not source.is_file():
        source = source.with_name("catalog.example.json")
    test_catalog_path = tmp_path / "catalog.json"
    test_catalog_path.write_bytes(source.read_bytes())
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": test_catalog_path,
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
    store = AccessStore(private_dir / "access.sqlite3", ["ИС 25/9-1П", "ИС 25/9-2П"])
    store.configure(group, password, is_open, None)
